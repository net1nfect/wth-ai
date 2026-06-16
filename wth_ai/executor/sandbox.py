"""Isolated subprocess sandbox for executor tasks."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig


class Sandbox:
    """Run commands in isolated temp directory with limits."""

    def __init__(self, config: WTHConfig, task_id: str) -> None:
        self.config = config
        self.task_id = task_id
        self.workdir = Path(tempfile.mkdtemp(prefix=f"wth-sandbox-{task_id[:8]}-"))
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "WTH_SANDBOX": "1",
        }

    def run(self, command: str, *, timeout: int | None = None) -> tuple[str, str, int]:
        timeout = timeout or self.config.tools.timeout_seconds
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.workdir,
                env=self.env,
            )
            stdout = proc.stdout[: self.config.tools.max_output_bytes]
            stderr = proc.stderr[:5000]
            return stdout, stderr, proc.returncode
        except subprocess.TimeoutExpired:
            return "", f"Sandbox timeout after {timeout}s", 124

    def cleanup(self) -> None:
        import shutil
        if self.workdir.exists():
            shutil.rmtree(self.workdir, ignore_errors=True)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "workdir": str(self.workdir)}
