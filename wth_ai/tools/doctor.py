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
    ok = (
      ws["complete"]
      and cfg["valid"]
      and vault["can_open"]
      and not refs["missing"]
      and perms["safe"]
    )
    return {
      "ok": ok,
      "workspace": ws,
      "config": cfg,
      "vault": vault,
      "referenced_secrets": refs,
      "permissions": perms,
    }

  def full_report(self) -> str:
    rt = self.check_runtime()
    ts = self.check_tools()
    wv = self.check_workspace_vault()
    ws, cfg, vault, refs, perms = (
      wv["workspace"], wv["config"], wv["vault"], wv["referenced_secrets"], wv["permissions"],
    )
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
