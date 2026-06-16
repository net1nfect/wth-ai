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
from wth_ai.workspace import SENSITIVE_FILES, wth_home, workspace_status


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

  def check_approval_registry(self) -> dict[str, Any]:
    try:
      from wth_ai.approvals import ApprovalRegistry
      reg = ApprovalRegistry()
      pending = len(reg.list_pending())
      writable = reg.path.parent.exists() or True  # parent created lazily on write
      return {"ok": True, "pending": pending, "writable": writable, "path": str(reg.path)}
    except Exception as exc:  # noqa: BLE001
      return {"ok": False, "pending": 0, "error": str(exc)}

  def check_tools(self, category: str | None = None) -> dict[str, Any]:
    summary = self.registry.summary()
    missing = self.registry.missing_tools()
    if category:
      missing = [t.id for t in self.registry.by_category(category) if not self.registry.is_installed(t.id)]
    return {
      **summary,
      "missing_sample": missing[:20],
    }

  def check_workspace_vault(self) -> dict[str, Any]:
    """Aggregate workspace + vault health (used by /doctor and `wth doctor`)."""
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
    }

  def full_report(self) -> str:
    rt = self.check_runtime()
    ts = self.check_tools()
    wv = self.check_workspace_vault()
    ws, cfg, vault, refs, perms = (
      wv["workspace"], wv["config"], wv["vault"], wv["referenced_secrets"], wv["permissions"],
    )
    roles, prov, prof = wv["roles"], wv["provider_secrets"], wv["profile"]
    bus, integ, appr = wv["event_bus"], wv["integrations"], wv["approvals"]
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
      f"- Go: {rt['go']}",
      f"- Pip: {rt['pip']}",
      "",
      "## Workspace & Vault",
      f"- Workspace: {'OK' if ws['complete'] else 'INCOMPLETE — run `wth setup`'}"
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
    lines.append("Run: `/tools install recon.subdomain` or `wth tools install all`")
    return "\n".join(lines)

  def auto_install(self, target: str = "all", *, use_sudo: bool = True) -> str:
    if target == "all":
      results = self.installer.install_all_missing(use_sudo=use_sudo, limit=30)
    else:
      results = self.installer.install_category(target, use_sudo=use_sudo)
    installed = sum(1 for r in results if r.get("status") in {"installed", "already_installed"})
    failed = [r for r in results if r.get("status") not in {"installed", "already_installed"}]
    lines = [
      f"## Tool Install — {installed}/{len(results)} succeeded",
      "",
    ]
    for r in results[:25]:
      lines.append(f"- {r.get('tool')}: {r.get('status')} ({r.get('method', '-')})")
    if failed:
      lines.append(f"\n{len(failed)} failed — check ~/.wth-ai/logs/install.log")
    return "\n".join(lines)

  def ensure_recon_tools(self, phases: list[int] | None = None) -> list[dict[str, Any]]:
    phases = phases or list(range(1, 6))
    results: list[dict[str, Any]] = []
    for p in phases:
      results.extend(self.installer.ensure_phase_tools(p))
    return results
