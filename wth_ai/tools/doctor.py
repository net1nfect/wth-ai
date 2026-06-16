"""Health check — runtime, API key, tools."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.tools.installer import ToolInstaller
from wth_ai.tools.registry import ToolRegistry


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

  def check_tools(self, category: str | None = None) -> dict[str, Any]:
    summary = self.registry.summary()
    missing = self.registry.missing_tools()
    if category:
      missing = [t.id for t in self.registry.by_category(category) if not self.registry.is_installed(t.id)]
    return {
      **summary,
      "missing_sample": missing[:20],
    }

  def full_report(self) -> str:
    rt = self.check_runtime()
    ts = self.check_tools()
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
