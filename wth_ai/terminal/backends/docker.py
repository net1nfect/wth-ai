"""Docker terminal backend for CTF / full_lab profiles."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from wth_ai.running_actions import get_running_actions, kill_process_tree


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
        # F1/WU-106: Popen (bukan subprocess.run) + registry -- `docker run`
        # adalah proses panjang; kill-switch harus menjangkau CLI-nya.
        proc = subprocess.Popen(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=(os.name != "nt"),
        )
        action_id = get_running_actions().register(proc.pid, label="docker:run")
        try:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc)
                proc.communicate()
                return {"backend": self.name, "error": "timeout", "exit_code": 124}
            return {
                "backend": self.name,
                "exit_code": proc.returncode,
                "stdout": out,
                "stderr": err,
            }
        finally:
            get_running_actions().unregister(action_id)


def _shell_quote(arg: str) -> str:
    if not arg:
        return "''"
    if all(c.isalnum() or c in "-_./" for c in arg):
        return arg
    return "'" + arg.replace("'", "'\"'\"'") + "'"
