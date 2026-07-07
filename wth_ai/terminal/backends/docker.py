"""Docker terminal backend for CTF / full_lab profiles."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class DockerTerminalBackend:
    image: str = "python:3.12-slim"
    name: str = "docker"

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def run(self, command: list[str], *, timeout: int = 120) -> dict[str, Any]:
        if not self.available():
            return {"backend": self.name, "error": "docker not available", "exit_code": 127}
        inner = " ".join(_shell_quote(a) for a in command)
        docker_cmd = [
            "docker", "run", "--rm", "--network=none",
            self.image, "sh", "-c", inner,
        ]
        try:
            proc = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return {"backend": self.name, "error": "timeout", "exit_code": 124}
        return {
            "backend": self.name,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def _shell_quote(arg: str) -> str:
    if not arg:
        return "''"
    if all(c.isalnum() or c in "-_./" for c in arg):
        return arg
    return "'" + arg.replace("'", "'\"'\"'") + "'"
