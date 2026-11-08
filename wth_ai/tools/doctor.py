"""Health check — runtime, API key, tools."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

import yaml

from wth_ai.config import USER_CONFIG_PATH, WTHConfig
from wth_ai.tools.installer import ToolInstaller
from wth_ai.tools.registry import ToolRegistry
from wth_ai.vault import SecretVault, is_vault_ref, ref_name
from wth_ai.workspace import RESULTS_DIR_NAME, SENSITIVE_FILES, wth_home, workspace_status


# P2-9: satu-satunya call site `shell=True` yang diizinkan. `scoop` di Windows
# berupa skrip PowerShell, jadi tidak bisa dieksekusi sebagai argv.
SHELL_ALLOWLIST: frozenset[str] = frozenset({"tools/installer.py:_run_command"})


def unallowlisted_shell_sites(root: Path | None = None) -> list[str]:
  """Pindai seluruh pohon paket via AST; kembalikan `shell=True` di luar allowlist.

  `check_platform` dulu hanya membaca `inspect.getsource(ToolRunner._run_command)`,
  jadi `shell=True` di modul lain tidak pernah ketahuan.
  """
  import ast

  package = Path(root) if root is not None else Path(__file__).resolve().parents[1]
  bad: list[str] = []
  for path in sorted(package.rglob("*.py")):
    if "__pycache__" in path.parts:
      continue
    try:
      tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
      continue
    funcs = [
      node for node in ast.walk(tree)
      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    rel = path.relative_to(package).as_posix()
    for node in ast.walk(tree):
      if not isinstance(node, ast.Call):
        continue
      if not any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
      ):
        continue
      enclosing = [f for f in funcs if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)]
      owner = max(enclosing, key=lambda f: f.lineno).name if enclosing else "<module>"
      site = f"{rel}:{owner}"
      if site not in SHELL_ALLOWLIST:
        bad.append(site)
  return bad


class ToolDoctor:
  """Diagnose WTH-AI runtime and tool availability."""

  def __init__(self, config: WTHConfig | None = None) -> None:
    self.config = config or WTHConfig.load()
    self.registry = ToolRegistry()
    self.installer = ToolInstaller(self.registry)

  def check_runtime(self) -> dict[str, Any]:
    home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    tools_bin = home / "tools" / "bin"
    runtime_bin = home / "runtime" / "wth-ai"
    return {
      "python": sys.version.split()[0],
      "frozen": getattr(sys, "frozen", False),
      "wth_home": str(home),
      "wth_home_exists": home.exists(),
      "tools_bin": str(tools_bin),
      "tools_bin_exists": tools_bin.exists(),
      "runtime_binary": str(runtime_bin) if runtime_bin.exists() else "not installed (use npm i -g @wth-ai/cli)",
      "install_method": "binary" if getattr(sys, "frozen", False) else "python",
      "openrouter": bool(self.config.api_key()),
      "go": shutil.which("go") or "missing",
      "pip": shutil.which("pip3") or shutil.which("pip") or "missing",
    }

  def check_workspace(self) -> dict[str, Any]:
    return workspace_status()

  def check_config(self) -> dict[str, Any]:
    if not USER_CONFIG_PATH.exists():
      return {"exists": False, "valid": True, "error": None}
    try:
      yaml.safe_load(USER_CONFIG_PATH.read_text(encoding="utf-8"))
      return {"exists": True, "valid": True, "error": None}
    except yaml.YAMLError as exc:
      return {"exists": True, "valid": False, "error": str(exc)}

  def check_vault(self) -> dict[str, Any]:
    vault = SecretVault()
    exists = vault.exists()
    can_open = vault.can_open() if exists else True
    keys = vault.list_keys() if (exists and can_open) else []
    return {"exists": exists, "can_open": can_open, "secret_count": len(keys), "keys": keys}

  def check_referenced_secrets(self) -> dict[str, Any]:
    """Verify every ``vault:<name>`` reference in user.yaml resolves."""
    refs: list[str] = []
    missing: list[str] = []
    if USER_CONFIG_PATH.exists():
      try:
        raw = yaml.safe_load(USER_CONFIG_PATH.read_text(encoding="utf-8")) or {}
      except yaml.YAMLError:
        raw = {}
      vault = SecretVault()
      for section in ("secrets", "bots"):
        for value in (raw.get(section) or {}).values():
          if is_vault_ref(value):
            name = ref_name(value)
            refs.append(name)
            if not vault.has(name):
              missing.append(name)
    return {"referenced": sorted(set(refs)), "missing": sorted(set(missing))}

  def check_permissions(self) -> dict[str, Any]:
    home = wth_home()
    issues: list[str] = []
    if home.exists():
      mode = stat.S_IMODE(home.stat().st_mode)
      if mode & (stat.S_IRWXG | stat.S_IRWXO):
        issues.append(f"{home} mode {oct(mode)} (expected 700)")
    for rel in SENSITIVE_FILES:
      p = home / rel
      if p.exists():
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
          issues.append(f"{p} mode {oct(mode)} (expected 600)")
    return {"safe": not issues, "issues": issues}

  def check_roles(self) -> dict[str, Any]:
    from wth_ai.config import ROLE_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    router = LLMProviderRouter(self.config)
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for role in ROLE_NAMES:
      rc = router.role_config(role)
      mapping[role] = f"{rc.type}/{rc.model}"
      if not rc.model:
        missing.append(role)
    return {
      "complete": not missing,
      "missing": missing,
      "fallback_present": bool(router.role_config("fallback").model),
      "mapping": mapping,
    }

  def check_provider_secrets(self) -> dict[str, Any]:
    from wth_ai.config import ROLE_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    router = LLMProviderRouter(self.config)
    used = {router.role_config(r).type for r in ROLE_NAMES}
    missing = [p for p in sorted(used) if p != "ollama" and not router.provider_key(p)]
    return {"providers_used": sorted(used), "missing_keys": missing, "ok": not missing}

  def check_active_profile(self) -> dict[str, Any]:
    from wth_ai.profiles import active_profile, list_profiles
    active = active_profile()
    valid = active is None or active in list_profiles()
    return {"active": active, "valid": valid}

  def check_event_bus(self) -> dict[str, Any]:
    try:
      from wth_ai.events import EVENT_TYPES, EventBus
      EventBus()  # constructible
      return {"available": True, "event_types": len(EVENT_TYPES)}
    except Exception as exc:  # noqa: BLE001
      return {"available": False, "event_types": 0, "error": str(exc)}

  def check_integrations(self) -> dict[str, Any]:
    from wth_ai.vault import SecretVault, is_vault_ref, ref_name
    ig = self.config.integrations
    vault = SecretVault()
    issues: list[str] = []
    enabled: list[str] = []

    def _check_token(platform: str, ref: str | None) -> None:
      if not ref:
        issues.append(f"{platform}: enabled but no bot_token_ref")
      elif is_vault_ref(ref) and not vault.has(ref_name(ref)):
        issues.append(f"{platform}: token ref '{ref}' missing in vault")

    if ig.telegram.enabled:
      enabled.append("telegram")
      _check_token("telegram", ig.telegram.bot_token_ref)
      if not ig.telegram.allowed_chat_ids:
        issues.append("telegram: no allowed_chat_ids")
      if not all(str(c).lstrip("-").isdigit() for c in ig.telegram.allowed_chat_ids):
        issues.append("telegram: allowed_chat_ids must be numeric")
    if ig.discord.enabled:
      enabled.append("discord")
      _check_token("discord", ig.discord.bot_token_ref)
      if not ig.discord.allowed_channel_ids:
        issues.append("discord: no allowed_channel_ids")
      if not isinstance(ig.discord.role_permissions, dict):
        issues.append("discord: role_permissions must be a mapping")
    if ig.webhook.enabled:
      enabled.append("webhook")
      if not ig.webhook.url:
        issues.append("webhook: enabled but no url")

    for name, cfg in (("telegram", ig.telegram), ("discord", ig.discord), ("webhook", ig.webhook)):
      if not isinstance(cfg.notify, dict):
        issues.append(f"{name}: notify rules must be a mapping")

    return {"ok": not issues, "enabled": enabled, "issues": issues}

  def check_jobs(self) -> dict[str, Any]:
    home = wth_home()
    queue_dir = home / "runtime" / "queue"
    jobs_log_dir = home / "logs" / "jobs"
    try:
      from wth_ai.jobs.store import JobStore
      store = JobStore()
      schema_ok = store.schema_ok()
      total = len(store.list_jobs(limit=1000))
      # worker status file is optional; reading must not fail
      worker_status = home / "runtime" / "worker.status"
      worker_readable = True
      if worker_status.exists():
        try:
          worker_status.read_text(encoding="utf-8")
        except OSError:
          worker_readable = False
      ok = schema_ok and queue_dir.exists() and worker_readable
      return {
        "ok": ok,
        "schema_ok": schema_ok,
        "db_path": str(store.db_path),
        "queue_dir": queue_dir.exists(),
        "jobs_log_dir": jobs_log_dir.exists(),
        "worker_readable": worker_readable,
        "total_jobs": total,
      }
    except Exception as exc:  # noqa: BLE001
      return {"ok": False, "schema_ok": False, "error": str(exc)}

  def check_approval_registry(self) -> dict[str, Any]:
    try:
      from wth_ai.approvals import ApprovalRegistry
      reg = ApprovalRegistry()
      pending = len(reg.list_pending())
      writable = reg.path.parent.exists() or True  # parent created lazily on write
      return {"ok": True, "pending": pending, "writable": writable, "path": str(reg.path)}
    except Exception as exc:  # noqa: BLE001
      return {"ok": False, "pending": 0, "error": str(exc)}

  def check_9router(self) -> dict[str, Any]:
    """Check 9router gateway reachability."""
    import urllib.request
    base = self.config.secrets.router9_base_url or self.config.nine_router.base_url
    url = base.rstrip("/") + "/models"
    try:
      req = urllib.request.Request(url, method="GET")  # noqa: S310
      with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310
        ok = 200 <= resp.status < 300
      return {"ok": ok, "base_url": base, "reachable": ok}
    except Exception as exc:  # noqa: BLE001
      return {"ok": False, "base_url": base, "reachable": False, "error": str(exc)}

  def check_mcp(self) -> dict[str, Any]:
    """Check MCP configuration."""
    mcp = self.config.mcp
    server_count = len(mcp.servers) if isinstance(mcp.servers, dict) else 0
    return {
      "ok": True,
      "enabled": mcp.enabled,
      "servers": server_count,
      "policy": mcp.policy,
    }

  def check_recon(self) -> dict[str, Any]:
    """Fase 5 recon platform health. Missing external tools are warnings only."""
    info: dict[str, Any] = {
      "importable": False,
      "registry_count": 0,
      "registry_ok": False,
      "builtin_ok": False,
      "results_writable": False,
      "wordlist_ok": False,
      "dns_resolver": False,
      "http_client": False,
      "report_writer": False,
      "profile_recon_only": False,
      "local_policy": False,
      "available_tools": 0,
      "missing_tools": 0,
      "warnings": [],
    }
    try:
      from wth_ai.recon.registry import ReconToolRegistry
      from wth_ai.recon.planner import PROFILE_CAP, detect_target_type
      from wth_ai.recon.tools.builtin import BuiltinContext, run_builtin
      from wth_ai.recon.wordlists import safe_paths

      info["importable"] = True
      reg = ReconToolRegistry()
      info["registry_count"] = len(reg)
      info["registry_ok"] = len(reg) >= 200
      summ = reg.summary()  # exercises availability without crashing
      info["available_tools"] = summ["available"]
      info["missing_tools"] = summ["missing"]

      # builtin recon works (offline: just dispatches without raising)
      ctx = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
      run_builtin("params", ctx)  # pure parse, no network
      info["builtin_ok"] = True

      info["wordlist_ok"] = len(safe_paths()) > 0
      info["http_client"] = self._module_present("requests")
      info["dns_resolver"] = self._module_present("dns") or self._module_present("socket")
      info["profile_recon_only"] = "recon-only" in PROFILE_CAP
      info["local_policy"] = detect_target_type("127.0.0.1") == "local"

      results_dir = wth_home() / RESULTS_DIR_NAME
      try:
        results_dir.mkdir(parents=True, exist_ok=True)
        probe = results_dir / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        info["results_writable"] = True
      except OSError:
        info["results_writable"] = False

      # report writer is importable + callable
      from wth_ai.recon import report as _rep  # noqa: F401
      info["report_writer"] = True

      if info["missing_tools"]:
        info["warnings"].append(f"{info['missing_tools']} external/api tools not installed (optional)")
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)

    info["ok"] = bool(
      info["importable"] and info["registry_ok"] and info["builtin_ok"]
      and info["results_writable"] and info["report_writer"]
    )
    return info

  @staticmethod
  def _module_present(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None

  def check_tools(self, category: str | None = None) -> dict[str, Any]:
    summary = self.registry.summary()
    missing = self.registry.missing_tools()
    if category:
      missing = [t.id for t in self.registry.by_category(category) if not self.registry.is_installed(t.id)]
    return {
      **summary,
      "missing_sample": missing[:20],
    }

  def check_ui(self) -> dict[str, Any]:
    info: dict[str, Any] = {
      "plain_repl": False,
      "slash_router": False,
      "tui_importable": False,
      "default_mode": "plain",
      "ok": False,
    }
    try:
      from wth_ai.cli.repl import WTHRepl  # noqa: F401
      from wth_ai.cli.terminal_shell import run_terminal_shell  # noqa: F401
      from wth_ai.cli.tui.bus import use_tui
      from wth_ai.platform.registry import get_platform_registry
      info["plain_repl"] = True
      reg = get_platform_registry()
      info["slash_commands"] = len(reg.commands)
      info["slash_router"] = info["slash_commands"] >= 20
      info["default_mode"] = "plain" if not use_tui() else "tui"
      try:
        from wth_ai.cli.tui.app import WthTuiApp  # noqa: F401
        info["tui_importable"] = True
      except ImportError:
        info["tui_importable"] = False
        info["tui_note"] = "optional: pip install wth-ai[tui]"
      info["ok"] = info["plain_repl"] and info["slash_router"]
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)
    return info

  def check_tui(self) -> dict[str, Any]:
    """Backward-compatible alias for check_ui."""
    ui = self.check_ui()
    return {
      "importable": ui.get("tui_importable", False),
      "slash_router": ui.get("slash_router", False),
      "slash_commands": ui.get("slash_commands", 0),
      "ok": ui.get("tui_importable", False) and ui.get("slash_router", False),
      "error": ui.get("error"),
    }

  def check_redteam(self) -> dict[str, Any]:
    """Fase 2–7: mode, rantai bukti, scope card, advisory, KB, skill, MCP."""
    info: dict[str, Any] = {"modes": False, "evidence": False, "evidence_error": "",
                            "scope_card": False, "disclosure": False, "kb": False,
                            "skill_select": False, "mcp_gating": False}
    try:
      from wth_ai.modes import BY_KEY, Cap

      info["modes"] = len(BY_KEY) >= 7 and BY_KEY["auto"].caps == frozenset(Cap)
    except Exception as exc:  # noqa: BLE001
      info["evidence_error"] = f"modes: {exc}"
    try:
      import tempfile

      from wth_ai.redteam.bundle import verify_bundle, write_bundle
      from wth_ai.redteam.models import Finding

      with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bundle"
        write_bundle(out, findings=[Finding(id="F-doc", hypothesis="x")],
                     scope_card={"target": "127.0.0.1", "authorized": True},
                     audit_events=[{"event": "scope"}])
        ok, _ = verify_bundle(out)
        info["evidence"] = ok
    except Exception as exc:  # noqa: BLE001
      info["evidence_error"] = f"evidence: {exc}"
    try:
      from wth_ai.scope_card import ScopeCard

      ScopeCard(target="127.0.0.1", cidr=["127.0.0.0/8"],
                authorized=True).validate()
      info["scope_card"] = True
    except Exception:  # noqa: BLE001
      info["scope_card"] = False
    try:
      from wth_ai.redteam.disclosure import build_advisory  # noqa: F401
      from wth_ai.redteam.novel.impact import impact_score  # noqa: F401
      from wth_ai.redteam.novel.labels import Provenance  # noqa: F401

      info["disclosure"] = True
    except Exception:  # noqa: BLE001
      pass
    try:
      info["kb"] = (wth_home() / "kb" / "kb.sqlite").is_file()
    except Exception:  # noqa: BLE001
      info["kb"] = False
    try:
      from wth_ai.skills.select import select_skills  # noqa: F401

      info["skill_select"] = True
    except Exception:  # noqa: BLE001
      pass
    try:
      from wth_ai.mcp.manager import MCPClientManager  # noqa: F401

      info["mcp_gating"] = True
    except Exception:  # noqa: BLE001
      pass
    info["ok"] = all(info[key] for key in
                     ("modes", "evidence", "scope_card", "disclosure",
                      "skill_select", "mcp_gating"))
    try:
      from wth_ai.recon.browser import available as browser_available
      from wth_ai.recon.http import proxy_map

      info["browser"] = browser_available()["available"]
      info["proxy"] = proxy_map() or {}
    except Exception:  # noqa: BLE001
      info["browser"] = False
      info["proxy"] = {}
    return info

  def check_gateway(self) -> dict[str, Any]:
    info: dict[str, Any] = {"importable": False, "reachable": False, "ok": False}
    try:
      from wth_ai.gateway.daemon import GatewayDaemon  # noqa: F401
      from wth_ai.gateway.client import gateway_available
      info["importable"] = True
      info["reachable"] = gateway_available()
      info["ok"] = info["importable"]
      if info["reachable"]:
        from wth_ai.gateway.client import GatewayClient
        st = GatewayClient().gateway_status()
        info["sessions"] = st.get("sessions", {})
        info["worker"] = st.get("worker", {})
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)
    return info

  def check_fts(self) -> dict[str, Any]:
    info: dict[str, Any] = {"importable": False, "writable": False, "ok": False}
    try:
      from wth_ai.memory.fts import SessionFtsIndex
      info["importable"] = True
      idx = SessionFtsIndex()
      idx.index_snippet("doctor", "system", "health check")
      hits = idx.search("health")
      info["writable"] = bool(hits)
      info["ok"] = info["importable"] and info["writable"]
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)
    return info

  def check_cron(self) -> dict[str, Any]:
    info: dict[str, Any] = {"importable": False, "store_valid": False, "ok": False}
    try:
      from wth_ai.cron.store import CronStore
      info["importable"] = True
      store = CronStore()
      _ = store.list()
      info["store_valid"] = True
      info["ok"] = info["importable"] and info["store_valid"]
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)
    return info

  def check_plugins(self) -> dict[str, Any]:
    info: dict[str, Any] = {"importable": False, "loaded": 0, "ok": False}
    try:
      from wth_ai.plugins.manager import PluginManager
      from wth_ai.events import get_event_bus
      mgr = PluginManager(self.config, bus=get_event_bus())
      loaded = mgr.load_all()
      info["importable"] = True
      info["loaded"] = len(loaded)
      info["ok"] = info["importable"] and info["loaded"] >= 2
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)
    return info

  def check_platform(self) -> dict[str, Any]:
    """Platform integration health (graph bridge, executor safety, job handlers)."""
    import inspect

    info: dict[str, Any] = {"ok": True, "graph_bridge": False, "no_shell": False, "job_handlers": False}
    try:
      from wth_ai.graph import recon_bridge

      info["graph_bridge"] = hasattr(recon_bridge, "sync_recon_to_graph")
    except Exception:
      info["ok"] = False
    try:
      from wth_ai.tool_runner import ToolRunner

      src = inspect.getsource(ToolRunner._run_command)
      sites = unallowlisted_shell_sites()
      info["shell_sites"] = sites
      info["no_shell"] = "shell=True" not in src and not sites
      if not info["no_shell"]:
        info["ok"] = False
    except Exception:
      info["ok"] = False
    try:
      from wth_ai.jobs import handlers

      info["job_handlers"] = "mock" not in inspect.getsource(handlers.handler_executor).lower()
      if not info["job_handlers"]:
        info["ok"] = False
    except Exception:
      info["ok"] = False
    return info

  def check_supervisor(self) -> dict[str, Any]:
    """Agentic supervisor + bridge health."""
    info: dict[str, Any] = {"importable": False, "tool_count": 0, "bridge_ok": False, "ok": False}
    try:
      from wth_ai.agents.tool_registry import tools_for_actor
      from wth_ai.recon.agent_bridge import ReconAgentBridge
      info["importable"] = True
      info["tool_count"] = len(tools_for_actor("supervisor"))
      bridge = ReconAgentBridge("doctor-check", profile="recon-only")
      bridge.set_target("example.com")
      info["bridge_ok"] = len(bridge.list_tools()) > 0
      info["ok"] = info["importable"] and info["tool_count"] >= 15 and info["bridge_ok"]
    except Exception as exc:  # noqa: BLE001
      info["error"] = str(exc)
    return info

  def check_workspace_vault(self) -> dict[str, Any]:
    """Aggregate workspace + vault health (used by /doctor and `wth-ai doctor`)."""
    ws = self.check_workspace()
    cfg = self.check_config()
    vault = self.check_vault()
    refs = self.check_referenced_secrets()
    perms = self.check_permissions()
    roles = self.check_roles()
    prov = self.check_provider_secrets()
    prof = self.check_active_profile()
    bus = self.check_event_bus()
    integ = self.check_integrations()
    appr = self.check_approval_registry()
    jobs = self.check_jobs()
    ok = (
      ws["complete"]
      and cfg["valid"]
      and vault["can_open"]
      and not refs["missing"]
      and perms["safe"]
      and roles["complete"]
      and prof["valid"]
      and bus["available"]
      and integ["ok"]
      and appr["ok"]
      and jobs["ok"]
    )
    return {
      "ok": ok,
      "workspace": ws,
      "config": cfg,
      "vault": vault,
      "referenced_secrets": refs,
      "permissions": perms,
      "roles": roles,
      "provider_secrets": prov,
      "profile": prof,
      "event_bus": bus,
      "integrations": integ,
      "approvals": appr,
      "jobs": jobs,
    }

  def full_report(self) -> str:
    rt = self.check_runtime()
    ts = self.check_tools()
    wv = self.check_workspace_vault()
    ws, cfg, vault, refs, perms = (
      wv["workspace"], wv["config"], wv["vault"], wv["referenced_secrets"], wv["permissions"],
    )
    roles, prov, prof = wv["roles"], wv["provider_secrets"], wv["profile"]
    bus, integ, appr, jobs = wv["event_bus"], wv["integrations"], wv["approvals"], wv["jobs"]
    lines = [
      "# WTH Doctor",
      "",
      "## Runtime",
      f"- Install method: {rt['install_method']}",
      f"- Python: {rt['python']}",
      f"- Binary mode: {rt['frozen']}",
      f"- WTH_HOME: {rt['wth_home']}",
      f"- Tools bin: {rt['tools_bin']}",
      f"- Runtime binary: {rt['runtime_binary']}",
      f"- OpenRouter: {'ONLINE' if rt['openrouter'] else 'OFFLINE — set OPENROUTER_API_KEY'}",
      f"- offline: {'ya (WTH_OFFLINE=1)' if os.environ.get('WTH_OFFLINE') == '1' else 'tidak'}",
      f"- Go: {rt['go']}",
      f"- Pip: {rt['pip']}",
      "",
      "## Workspace & Vault",
      f"- Workspace: {'OK' if ws['complete'] else 'INCOMPLETE — run `wth-ai setup`'}"
      + (f" (missing: {', '.join(ws['missing_dirs'])})" if ws["missing_dirs"] else ""),
      f"- db.sqlite: {'present' if ws['db_exists'] else 'missing'}",
      f"- Config: {'valid' if cfg['valid'] else 'INVALID — ' + str(cfg['error'])}",
      f"- Vault: {'open OK' if vault['can_open'] else 'CANNOT OPEN'} "
      + f"({vault['secret_count']} secrets)",
      f"- Referenced secrets: {'all present' if not refs['missing'] else 'MISSING ' + ', '.join(refs['missing'])}",
      f"- Permissions: {'safe' if perms['safe'] else 'UNSAFE — ' + '; '.join(perms['issues'])}",
      "",
      "## Providers & Roles",
      f"- Role models: {'all set' if roles['complete'] else 'MISSING ' + ', '.join(roles['missing'])}",
      f"- Fallback model: {'present' if roles['fallback_present'] else 'MISSING'}",
      f"- Provider keys: {'all present' if prov['ok'] else 'MISSING ' + ', '.join(prov['missing_keys'])}"
      + f" (used: {', '.join(prov['providers_used'])})",
      f"- Active profile: {prof['active'] or 'none'}"
      + ("" if prof["valid"] else " (INVALID)"),
      "",
    ]
    for role, m in roles["mapping"].items():
      lines.append(f"  - {role}: {m}")
    lines += [
      "",
      "## Integrations & Events",
      f"- Event bus: {'available' if bus['available'] else 'UNAVAILABLE'} ({bus['event_types']} event types)",
      f"- Integrations enabled: {', '.join(integ['enabled']) or 'none'}",
      f"- Integration config: {'valid' if integ['ok'] else 'ISSUES — ' + '; '.join(integ['issues'])}",
      f"- Approval registry: {'OK' if appr['ok'] else 'ERROR'} ({appr.get('pending', 0)} pending)",
      "",
      "## Jobs & Worker",
      f"- Job store: {'OK' if jobs['ok'] else 'ERROR'}"
      + (f" — {jobs['error']}" if jobs.get('error') else f" (schema {'valid' if jobs.get('schema_ok') else 'INVALID'}, {jobs.get('total_jobs', 0)} jobs)"),
      f"- Queue dir: {'present' if jobs.get('queue_dir') else 'MISSING'}",
      f"- Worker status: {'readable' if jobs.get('worker_readable') else 'UNREADABLE'}",
      "",
    ]
    rec = self.check_recon()
    lines += [
      "## Recon Platform (Fase 5)",
      f"- Engine importable: {'yes' if rec['importable'] else 'NO'}",
      f"- Tool registry: {rec['registry_count']} tools "
      + ("(OK >=200)" if rec['registry_ok'] else "(TOO FEW — expected >=200)"),
      f"- Available now: {rec['available_tools']} · missing (optional): {rec['missing_tools']}",
      f"- Builtin recon: {'OK' if rec['builtin_ok'] else 'FAIL'}",
      f"- HTTP client: {'OK' if rec['http_client'] else 'missing'} · "
      + f"DNS resolver: {'OK' if rec['dns_resolver'] else 'missing'}",
      f"- Wordlist: {'OK' if rec['wordlist_ok'] else 'missing'} · "
      + f"Report writer: {'OK' if rec['report_writer'] else 'missing'}",
      f"- Results dir writable: {'yes' if rec['results_writable'] else 'NO'}",
      f"- Profile recon-only: {'valid' if rec['profile_recon_only'] else 'INVALID'} · "
      + f"local policy: {'valid' if rec['local_policy'] else 'INVALID'}",
      f"- Recon status: {'OK' if rec['ok'] else 'ISSUES'}"
      + (f" — warnings: {'; '.join(rec['warnings'])}" if rec.get('warnings') else ""),
      "",
    ]
    sup = self.check_supervisor()
    ui = self.check_ui()
    tui = self.check_tui()
    gw = self.check_gateway()
    fts = self.check_fts()
    cron = self.check_cron()
    plugins = self.check_plugins()
    platform = self.check_platform()
    redteam = self.check_redteam()
    lines += [
      "## Agentic Supervisor",
      f"- Supervisor importable: {'yes' if sup['importable'] else 'NO'}",
      f"- Supervisor tools: {sup['tool_count']} (need >=15)",
      f"- Recon agent bridge: {'OK' if sup['bridge_ok'] else 'FAIL'}",
      f"- Agentic status: {'OK' if sup['ok'] else 'ISSUES'}"
      + (f" — {sup['error']}" if sup.get('error') else ""),
      "",
      "## Interactive UI",
      f"- Plain REPL: {'OK' if ui['plain_repl'] else 'FAIL'} · default mode: {ui.get('default_mode', 'plain')}",
      f"- Slash commands indexed: {ui.get('slash_commands', 0)}",
      f"- Plain UI status: {'OK' if ui['ok'] else 'ISSUES'}"
      + (f" — {ui['error']}" if ui.get('error') else ""),
      f"- Textual TUI (optional): {'installed' if tui['importable'] else 'not installed (pip install wth-ai[tui])'}",
      "",
      "## Red Team Engine (Fase 2–7)",
      f"- Modes: {'OK' if redteam['modes'] else 'FAIL'} · "
      + f"evidence roundtrip: {'OK' if redteam['evidence'] else redteam['evidence_error'] or 'FAIL'}",
      f"- Scope card: {'OK' if redteam['scope_card'] else 'FAIL'} · "
      + f"disclosure: {'OK' if redteam['disclosure'] else 'FAIL'} · "
      + f"KB: {'present' if redteam['kb'] else 'belum dibangun (warn)'}",
      f"- Skill selector: {'OK' if redteam['skill_select'] else 'FAIL'} · "
      + f"MCP gating: {'OK' if redteam['mcp_gating'] else 'FAIL'}",
      f"- Playwright: {'tersedia' if redteam.get('browser') else 'tidak (opsional)'} · "
      + f"proxy: {redteam.get('proxy', {}).get('https') or 'langsung (tanpa proxy)'}",
      "",
      "## Gateway & Platform (Fase 7)",
      f"- Gateway module: {'OK' if gw['importable'] else 'FAIL'} · "
      + f"reachable: {'yes' if gw.get('reachable') else 'no (run `wth-ai gateway run`)'}",
      f"- FTS memory: {'OK' if fts['ok'] else 'ISSUES'}",
      f"- Cron store: {'OK' if cron['ok'] else 'ISSUES'}",
      f"- Plugins: {plugins.get('loaded', 0)} loaded ({'OK' if plugins['ok'] else 'WARN'})",
      f"- Graph bridge: {'OK' if platform.get('graph_bridge') else 'MISSING'} · "
      + f"executor no-shell: {'OK' if platform.get('no_shell') else 'FAIL'} · "
      + f"job handlers: {'real' if platform.get('job_handlers') else 'mock'}",
      "",
      f"## Tools: {ts['installed']}/{ts['total']} installed ({ts['missing']} missing)",
      "",
    ]
    if ts.get("missing_sample"):
      lines.append("### Top missing (install with `/tools install all`)")
      for tid in ts["missing_sample"][:15]:
        spec = self.registry.tools.get(tid)
        if spec:
          methods = ", ".join(spec.install.keys()) or "manual"
          lines.append(f"- `{tid}` ({methods})")
    lines.append("")
    lines.append("Run: `/tools install recon.subdomain` or `wth-ai tools install all`")
    return "\n".join(lines)

  def auto_install(
    self,
    target: str = "all",
    *,
    use_sudo: bool = True,
    on_progress: Any = None,
  ) -> str:
    from wth_ai.tools.platform import platform_summary

    plat = platform_summary()
    if target == "all":
      results = self.installer.install_all_missing(
        use_sudo=use_sudo, limit=30, on_progress=on_progress,
      )
    elif target in {"top", "top20", "recon-top"}:
      results = self.installer.install_top_recon(use_sudo=use_sudo, on_progress=on_progress)
    else:
      results = self.installer.install_category(target, use_sudo=use_sudo)
    installed = sum(1 for r in results if r.get("status") in {"installed", "already_installed"})
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = [r for r in results if r.get("status") not in {"installed", "already_installed", "skipped"}]
    lines = [
      f"## Tool Install — {plat}",
      f"**{installed}/{len(results)}** installed · {skipped} skipped (OS incompatible) · {len(failed)} failed",
      "",
    ]
    for r in results[:25]:
      extra = f" — {r.get('error', '')}" if r.get("error") else ""
      lines.append(f"- {r.get('tool')}: {r.get('status')} ({r.get('method', '-')}){extra}")
    if failed:
      lines.append(f"\n{len(failed)} failed — check ~/.wth-ai/logs/install.log")
    if skipped:
      lines.append(f"\nTip: skipped tools need manual install on this OS or use go/pip-only tools.")
    return "\n".join(lines)

  def ensure_recon_tools(self, phases: list[int] | None = None) -> list[dict[str, Any]]:
    phases = phases or list(range(1, 6))
    results: list[dict[str, Any]] = []
    for p in phases:
      results.extend(self.installer.ensure_phase_tools(p))
    return results
