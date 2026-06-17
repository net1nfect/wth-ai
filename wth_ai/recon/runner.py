# wth_ai/recon/runner.py
"""
Tool Runner (Fase 5.3 - Foundation)

Safe execution of external tools.

Principles (from AGENTS.md + Fase 5 spec):
- Never use shell=True
- Always timeout
- Redact secrets from output
- Respect permission levels
- Return structured ToolRunResult
"""

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import ToolSpec, ToolRunResult


def run_tool(
    tool: ToolSpec,
    args: list[str],
    timeout: int = 120,
    cwd: Optional[Path] = None,
) -> ToolRunResult:
    """
    Run a single tool safely.

    This is a safe foundation. Full implementation (redaction, artifact saving,
    rate limiting) will be expanded in later sub-phases.
    """
    started = datetime.now(timezone.utc).isoformat()
    start_time = time.time()

    try:
        # Safe execution - no shell=True
        result = subprocess.run(
            [tool.binary] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )

        finished = datetime.now(timezone.utc).isoformat()
        duration = int((time.time() - start_time) * 1000)

        status = "success" if result.returncode == 0 else "failed"

        return ToolRunResult(
            tool=tool.name,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_ms=duration,
            output_path=None,  # Will be implemented later
            error=result.stderr[:500] if result.stderr else None,
        )

    except subprocess.TimeoutExpired:
        return ToolRunResult(
            tool=tool.name,
            status="timeout",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int((time.time() - start_time) * 1000),
            error=f"Tool timed out after {timeout}s",
        )
    except FileNotFoundError:
        return ToolRunResult(
            tool=tool.name,
            status="skipped",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error="Binary not found in PATH",
        )
    except Exception as e:
        return ToolRunResult(
            tool=tool.name,
            status="failed",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
