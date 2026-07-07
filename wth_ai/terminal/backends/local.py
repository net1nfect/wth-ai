"""Local shell terminal backend."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class LocalTerminalBackend:
    name: str = "local"

    def run(self, command: list[str], *, cwd: str | None = None, timeout: int = 60) -> dict[str, Any]:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            shell=False,
        )
        return {
            "backend": self.name,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
