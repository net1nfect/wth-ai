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
        from wth_ai.shell_runner import run_command

        timeout = timeout or self.config.tools.timeout_seconds
        return run_command(
            command,
            timeout=timeout,
            cwd=str(self.workdir),
            env=self.env,
            max_bytes=self.config.tools.max_output_bytes,
        )

    def cleanup(self) -> None:
        import shutil
        if self.workdir.exists():
            shutil.rmtree(self.workdir, ignore_errors=True)

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "workdir": str(self.workdir)}
