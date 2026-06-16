"""Auto-install missing security tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.tools.registry import ToolRegistry, ToolSpec


class ToolInstaller:
  """Install tools via go/apt/brew/pip with logging."""

  INSTALL_ORDER = ("go", "pip", "npm", "gem", "cargo", "apt", "brew", "script")

  def __init__(self, registry: ToolRegistry | None = None) -> None:
    self.registry = registry or ToolRegistry()
    home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    self.tools_bin = home / "tools" / "bin"
    self.tools_bin.mkdir(parents=True, exist_ok=True)
    self.log_dir = home / "logs"
    self.log_dir.mkdir(parents=True, exist_ok=True)
    self.log_file = self.log_dir / "install.log"
    os.environ.setdefault("GOBIN", str(self.tools_bin))
    path = os.environ.get("PATH", "")
    if str(self.tools_bin) not in path.split(os.pathsep):
      os.environ["PATH"] = f"{self.tools_bin}{os.pathsep}{path}"

  def _log(self, msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}\n"
    with self.log_file.open("a", encoding="utf-8") as f:
      f.write(line)

  def _pick_installer(self, spec: ToolSpec) -> tuple[str, str] | None:
    for method in self.INSTALL_ORDER:
      if method in spec.install:
        if method == "go" and not shutil.which("go"):
          continue
        if method == "apt" and not shutil.which("apt-get"):
          continue
        if method == "brew" and not shutil.which("brew"):
          continue
        if method == "pip" and not shutil.which("pip") and not shutil.which("pip3"):
          continue
        return method, spec.install[method]
    return None

  def install_one(self, tool_id: str, *, use_sudo: bool = True) -> dict[str, Any]:
    spec = self.registry.tools.get(tool_id)
    if not spec:
      return {"tool": tool_id, "status": "unknown", "error": "not in manifest"}
    if self.registry.is_installed(tool_id):
      return {"tool": tool_id, "status": "already_installed"}

    picked = self._pick_installer(spec)
    if not picked:
      return {"tool": tool_id, "status": "no_installer", "error": "no compatible install method"}

    method, cmd = picked
    if method == "apt" and use_sudo:
      cmd = f"sudo {cmd}"

    self._log(f"INSTALL {tool_id}: {cmd}")
    try:
      proc = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "GOBIN": str(self.tools_bin)},
      )
      ok = proc.returncode == 0 or self.registry.is_installed(tool_id)
      return {
        "tool": tool_id,
        "status": "installed" if ok else "failed",
        "method": method,
        "command": cmd,
        "stdout": proc.stdout[-500:] if proc.stdout else "",
        "stderr": proc.stderr[-500:] if proc.stderr else "",
      }
    except subprocess.TimeoutExpired:
      return {"tool": tool_id, "status": "timeout", "command": cmd}
    except OSError as exc:
      return {"tool": tool_id, "status": "error", "error": str(exc)}

  def install_category(self, category: str, *, use_sudo: bool = True) -> list[dict[str, Any]]:
    missing = [t for t in self.registry.by_category(category) if not self.registry.is_installed(t.id)]
    return [self.install_one(t.id, use_sudo=use_sudo) for t in missing]

  def install_all_missing(self, *, use_sudo: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    missing = self.registry.missing_tools()[:limit]
    return [self.install_one(tid, use_sudo=use_sudo) for tid in missing]

  def ensure_phase_tools(self, phase: int, *, use_sudo: bool = True) -> list[dict[str, Any]]:
    tools = [t for t in self.registry.by_phase(phase) if not self.registry.is_installed(t.id)]
    results = []
    for spec in tools[:10]:
      results.append(self.install_one(spec.id, use_sudo=use_sudo))
    return results
