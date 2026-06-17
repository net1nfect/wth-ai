# wth_ai/recon/runner.py
"""
Tool Runner - Fase 5.3 (Improved)

Safe execution with:
- No shell=True
- Timeout
- Basic secret redaction
- Artifact path support

Still non-destructive by default.
"""

import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Pattern

from .models import ToolSpec, ToolRunResult


# Basic secret patterns to redact (can be expanded)
SECRET_PATTERNS: list[Pattern] = [
    re.compile(r'(?i)(api[_-]?key|token|secret|password|auth)["\s:=]+[\w\-\.]+'),
    re.compile(r'(?i)(sk-[a-zA-Z0-9]{20,})'),  # OpenAI-style keys
    re.compile(r'(?i)(Bearer\s+[\w\-\.]+)'),
]


def _redact_secrets(text: str) -> str:
    if not text:
        return text
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(r"\1=****REDACTED****", redacted)
    return redacted


def run_tool(
    tool: ToolSpec,
    args: list[str],
    timeout: int = 120,
    cwd: Optional[Path] = None,
    redact_output: bool = True,
    save_artifact: bool = False,
    artifact_dir: Optional[Path] = None,
) -> ToolRunResult:
    """
    Run a reconnaissance tool safely.

    Args:
        tool: ToolSpec from registry
        args: Arguments to pass to the tool
        timeout: Max seconds to wait
        cwd: Working directory
        redact_output: Whether to redact secrets from stdout/stderr
        save_artifact: Whether to save raw output to file
        artifact_dir: Where to save artifacts (defaults to ~/.wth-ai/wth-results/<job_id>/raw/)
    """
    started = datetime.now(timezone.utc).isoformat()
    start_time = time.time()

    stdout = ""
    stderr = ""

    try:
        proc = subprocess.run(
            [tool.binary] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            check=False,
        )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        if redact_output:
            stdout = _redact_secrets(stdout)
            stderr = _redact_secrets(stderr)

        finished = datetime.now(timezone.utc).isoformat()
        duration = int((time.time() - start_time) * 1000)

        status = "success" if proc.returncode == 0 else "failed"

        output_path = None
        if save_artifact and artifact_dir:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", tool.name)
            output_path = artifact_dir / f"{safe_name}_{int(time.time())}.txt"
            output_path.write_text(stdout + "\n--- STDERR ---\n" + stderr, encoding="utf-8")

        return ToolRunResult(
            tool=tool.name,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_ms=duration,
            output_path=str(output_path) if output_path else None,
            error=stderr[:800] if stderr else None,
        )

    except subprocess.TimeoutExpired:
        return ToolRunResult(
            tool=tool.name,
            status="timeout",
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int((time.time() - start_time) * 1000),
            error=f"Timed out after {timeout} seconds",
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
