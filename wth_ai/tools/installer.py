"""Auto-install missing security tools — cross-platform (Linux, macOS, Windows)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from wth_ai.tools.platform import (
    augment_install_spec,
    compatible_methods,
    detect_os,
    install_order,
    normalize_install_command,
    platform_summary,
)
from wth_ai.tools.registry import ToolRegistry, ToolSpec

TOP_RECON_TOOLS: tuple[str, ...] = (
    "subfinder", "httpx", "dnsx", "nuclei", "naabu", "katana", "gau",
    "waybackurls", "ffuf", "gobuster", "nmap", "whatweb", "tlsx",
    "amass", "assetfinder", "findomain", "paramspider", "arjun",
    "feroxbuster", "jq",
)


class ToolInstaller:
    """Install tools via go/pip/brew/apt/winget/choco and other package managers."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()
        self.os_info = detect_os()
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
        install = augment_install_spec(spec.id, spec.install)
        compat = set(compatible_methods(install, spec.id))
        for method in install_order():
            if method in install and method in compat:
                return method, install[method]
        return None

    def _run_command(self, method: str, cmd: str, *, use_sudo: bool) -> subprocess.CompletedProcess[str]:
        argv, display, shell = normalize_install_command(method, cmd, use_sudo=use_sudo)
        env = {**os.environ, "GOBIN": str(self.tools_bin)}
        if shell or argv is None:
            return subprocess.run(
                display,
                shell=True,
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
        return subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
        )

    def install_one(
        self,
        tool_id: str,
        *,
        use_sudo: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        spec = self.registry.tools.get(tool_id)
        if not spec:
            return {"tool": tool_id, "status": "unknown", "error": "not in manifest"}
        if self.registry.is_installed(tool_id):
            return {"tool": tool_id, "status": "already_installed"}

        picked = self._pick_installer(spec)
        if not picked:
            methods = list(spec.install.keys()) or ["manual"]
            return {
                "tool": tool_id,
                "status": "no_installer",
                "error": f"no compatible install on {self.os_info.system} ({', '.join(methods)})",
                "platform": platform_summary(),
            }

        method, cmd = picked
        _, display, _ = normalize_install_command(method, cmd, use_sudo=use_sudo)
        if on_progress:
            on_progress(f"install {tool_id} via {method}…")
        self._log(f"INSTALL {tool_id} [{self.os_info.system}]: {display}")
        try:
            proc = self._run_command(method, cmd, use_sudo=use_sudo)
            ok = proc.returncode == 0 or self.registry.is_installed(tool_id)
            if on_progress:
                status = "ok" if ok else "failed"
                on_progress(f"{tool_id}: {status} ({method})")
            return {
                "tool": tool_id,
                "status": "installed" if ok else "failed",
                "method": method,
                "command": display,
                "platform": self.os_info.system,
                "stdout": proc.stdout[-500:] if proc.stdout else "",
                "stderr": proc.stderr[-500:] if proc.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"tool": tool_id, "status": "timeout", "command": display, "method": method}
        except OSError as exc:
            return {"tool": tool_id, "status": "error", "error": str(exc), "method": method}

    def install_category(self, category: str, *, use_sudo: bool = True) -> list[dict[str, Any]]:
        missing = [t for t in self.registry.by_category(category) if not self.registry.is_installed(t.id)]
        return [self.install_one(t.id, use_sudo=use_sudo) for t in missing]

    def install_all_missing(
        self,
        *,
        use_sudo: bool = True,
        limit: int = 50,
        skip_incompatible: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        missing = self.registry.missing_tools()
        results: list[dict[str, Any]] = []
        for tid in missing:
            if len(results) >= limit:
                break
            spec = self.registry.tools.get(tid)
            if spec and skip_incompatible and not self._pick_installer(spec):
                results.append({
                    "tool": tid,
                    "status": "skipped",
                    "error": "no compatible installer on this OS",
                    "platform": self.os_info.system,
                })
                continue
            results.append(self.install_one(tid, use_sudo=use_sudo, on_progress=on_progress))
        return results

    def install_top_recon(
        self,
        *,
        use_sudo: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Install priority recon tools from TOP_RECON_TOOLS."""
        results: list[dict[str, Any]] = []
        for tid in TOP_RECON_TOOLS:
            if self.registry.is_installed(tid):
                continue
            if tid not in self.registry.tools:
                continue
            results.append(self.install_one(tid, use_sudo=use_sudo, on_progress=on_progress))
        return results

    def ensure_phase_tools(self, phase: int, *, use_sudo: bool = True) -> list[dict[str, Any]]:
        tools = [t for t in self.registry.by_phase(phase) if not self.registry.is_installed(t.id)]
        results = []
        for spec in tools[:10]:
            results.append(self.install_one(spec.id, use_sudo=use_sudo))
        return results

    def platform_report(self) -> str:
        return platform_summary()
