 cursor/fase5-recon-platform-93fe
"""Safe tool runner for the recon platform.

Hard safety guarantees for external tools:
- **no ``shell=True``** — args are always a list.
- mandatory timeout.
- sanitized environment (no inherited secrets/tokens).
- stdout/stderr size cap.
- secret redaction before anything is written to disk/log.
- per-tool artifact + JSONL run log.
"""

from __future__ import annotations

import json
import os

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
 main
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
cursor/fase5-recon-platform-93fe
from typing import Any

from wth_ai.recon.model import ToolRun, ToolSpec
from wth_ai.recon.registry import ReconToolRegistry
from wth_ai.recon.tools.adapters import get_adapter
from wth_ai.recon.tools.builtin import BuiltinContext, run_builtin
from wth_ai.recon.tools.catalog import BUILTIN_TOOLS

MAX_OUTPUT_BYTES = 200_000
DEFAULT_TIMEOUT = 60

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"), r"\1=***REDACTED***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ***REDACTED***"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AKIA***REDACTED***"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"), "***JWT-REDACTED***"),
)


def redact(text: str) -> str:
    out = text or ""
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _sanitized_env() -> dict[str, str]:
    """Minimal env with no inherited secrets/tokens."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    # ensure WTH tools bin is searchable for external binaries
    from wth_ai.workspace import wth_home

    bin_dir = str(wth_home() / "tools" / "bin")
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SafeToolRunner:
    def __init__(
        self,
        registry: ReconToolRegistry,
        *,
        raw_dir: Path | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        runlog_path: Path | None = None,
    ) -> None:
        self.registry = registry
        self.raw_dir = raw_dir
        self.timeout = timeout
        self.runlog_path = runlog_path

    # --- public API ---
    def run(self, spec: ToolSpec, *, target: str, ctx: BuiltinContext) -> ToolRun:
        start = time.time()
        started = _now()
        run = ToolRun(tool=spec.name, started_at=started)
        try:
            if spec.execution_mode == "builtin":
                self._run_builtin(spec, ctx, run)
            elif spec.execution_mode == "external":
                self._run_external(spec, target, run)
            elif spec.execution_mode == "api":
                run.status = "skipped"
                run.error = "api adapter not implemented (key present but no live call)"
            else:  # manual / stub
                run.status = "skipped"
                run.error = f"{spec.execution_mode}: not auto-runnable"
        except Exception as exc:  # noqa: BLE001 — never let a tool break the engine
            run.status = "failed"
            run.error = redact(str(exc))

        run.finished_at = _now()
        run.duration_ms = int((time.time() - start) * 1000)
        self._write_runlog(run)
        return run

    # --- builtin path ---
    def _run_builtin(self, spec: ToolSpec, ctx: BuiltinContext, run: ToolRun) -> None:
        key = BUILTIN_TOOLS.get(spec.name)
        if key is None:
            run.status = "skipped"
            run.error = "no builtin mapping"
            return
        items = run_builtin(key, ctx)
        run.normalized_items = items
        run.status = "success"
        run.output_path = self._write_raw(spec.name, json.dumps(items, indent=2))

    # --- external path ---
    def _run_external(self, spec: ToolSpec, target: str, run: ToolRun) -> None:
        if os.environ.get("WTH_TEST_MODE"):
            run.status = "skipped"
            run.error = "test-mode: external execution disabled"
            return
        if not self.registry.is_available(spec.name):
            run.status = "skipped"
            run.error = "binary not found on PATH"
            return
        adapter = get_adapter(spec.name)
        if adapter is None:
            run.status = "skipped"
            run.error = "no adapter (manual parsing required)"
            return
        argv = adapter.build_args(target)
        try:
            proc = subprocess.run(  # noqa: S603 — argv list, shell=False, sanitized env
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=_sanitized_env(),
                cwd=str(self.raw_dir) if self.raw_dir else None,
            )
        except subprocess.TimeoutExpired:
            run.status = "timeout"
            run.error = f"timeout after {self.timeout}s"
            return
        except (OSError, ValueError) as exc:
            run.status = "failed"
            run.error = redact(str(exc))
            return

        stdout = redact((proc.stdout or "")[:MAX_OUTPUT_BYTES])
        run.output_path = self._write_raw(spec.name, stdout)
        run.normalized_items = adapter.parse(stdout)
        run.status = "success" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            run.error = redact((proc.stderr or "")[:2000]) or f"exit {proc.returncode}"

    # --- artifacts ---
    def _write_raw(self, tool: str, content: str) -> str | None:
        if self.raw_dir is None:
            return None
        try:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_.\-]", "_", tool)
            path = self.raw_dir / f"{safe_name}.txt"
            path.write_text(redact(content), encoding="utf-8")
            return str(path)
        except OSError:
            return None

    def _write_runlog(self, run: ToolRun) -> None:
        if self.runlog_path is None:
            return
        try:
            self.runlog_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "tool": run.tool,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_ms": run.duration_ms,
                "output_path": run.output_path,
                "error": run.error,
                "items": len(run.normalized_items),
            }
            with self.runlog_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass
=======
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
main
