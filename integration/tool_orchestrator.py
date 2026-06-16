"""Allowlisted external tool orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.exceptions import ToolError


class ToolOrchestrator:
    """Execute only explicitly allowlisted commands without a shell."""

    def __init__(self, allowed_tools: dict[str, str] | None = None) -> None:
        self.allowed_tools = allowed_tools or {}

    async def execute(self, name: str, arguments: list[str], timeout: int = 60) -> dict[str, Any]:
        """Execute an allowlisted tool and capture bounded output."""
        executable = self.allowed_tools.get(name)
        if not executable or not Path(executable).is_file():
            raise ToolError(f"Tool is not allowlisted or unavailable: {name}")
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ToolError(f"Tool timed out: {name}") from exc
        return {
            "returncode": process.returncode,
            "stdout": stdout.decode(errors="replace")[-100_000:],
            "stderr": stderr.decode(errors="replace")[-100_000:],
        }
