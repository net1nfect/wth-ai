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
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.recon.model import ToolRun, ToolSpec
from wth_ai.recon.registry import ReconToolRegistry
from wth_ai.recon.tools.adapters import get_adapter
from wth_ai.recon.tools.builtin import BuiltinContext, run_builtin
from wth_ai.recon.tools.catalog import BUILTIN_TOOLS

from wth_ai.running_actions import get_running_actions, kill_process_tree

MAX_OUTPUT_BYTES = 200_000
DEFAULT_TIMEOUT = 60

def redact(text: str) -> str:
    """WU-203: delegate to the canonical `wth_ai.redaction.redact_secrets`
    (this module's pattern set was the most complete and became the seed for
    it). Kept as a named re-export so existing callers/tests are unchanged."""
    from wth_ai.redaction import redact_secrets

    return redact_secrets(text)


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


def write_runlog_entry(runlog_path: Path | None, run: ToolRun) -> None:
    """Append one `tool_runs.jsonl` entry. THE single writer for this file.

    The engine's builtin-fingerprint step used to append its own reduced object
    (`{tool, status, items}`), so exactly one line of tool_runs.jsonl had a
    different shape from every other line and any consumer expecting the full
    record broke on it. Routing every producer through here makes that
    divergence impossible.
    """
    if runlog_path is None:
        return
    try:
        runlog_path.parent.mkdir(parents=True, exist_ok=True)
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
        with runlog_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


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
                self._run_api(spec, target, run)
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

    # Test seam: the call that actually spawns the child process. Defaults to
    # the real `subprocess.Popen`; tests replace it to exercise the external
    # path without running a binary.
    def _spawn(self, argv: list[str], **kwargs: Any) -> Any:
        return subprocess.Popen(argv, **kwargs)

    def _run_bounded(self, argv: list[str]) -> tuple[str, str, int]:
        """Run `argv`, keeping at most MAX_OUTPUT_BYTES of each stream in memory.

        `subprocess.run(capture_output=True)` buffers the ENTIRE stdout/stderr
        before the 200KB cap is applied, so a tool printing 150MB pushed the
        worker's RSS up by that much (measured: +429MB) before the slice threw
        the rest away -- an OOM for a large wordlist run. Here the retained
        buffer is bounded while the pipe is still fully drained (a child blocked
        on a full pipe would otherwise hang until the timeout).

        Also guarantees `cwd` exists: `raw_dir` may not have been created yet
        when this runs outside the engine (the engine happens to create it via a
        builtin first), and `Popen(cwd=<missing>)` raises FileNotFoundError,
        which surfaced as a spurious tool failure.
        """
        import threading

        cwd: str | None = None
        if self.raw_dir is not None:
            try:
                self.raw_dir.mkdir(parents=True, exist_ok=True)
                cwd = str(self.raw_dir)
            except OSError:
                cwd = None

        proc = self._spawn(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=_sanitized_env(),
            cwd=cwd,
            # F1/WU-106: sesi sendiri -> pgid == pid; kill-switch (dan kill saat
            # timeout) menjangkau seluruh proses tree. Test-seam `_spawn` tiruan
            # menerima **kwargs, jadi kwarg tambahan ini aman.
            start_new_session=(os.name != "nt"),
        )
        action_id = get_running_actions().register(
            getattr(proc, "pid", 0) or 0,
            label=("recon:" + os.path.basename(argv[0]))[:100] if argv else "recon",
        )

        def _drain(stream: Any) -> bytes:
            kept = bytearray()
            try:
                while True:
                    chunk = stream.read(65536)
                    if not chunk:
                        break
                    if len(kept) < MAX_OUTPUT_BYTES:
                        kept.extend(chunk[: MAX_OUTPUT_BYTES - len(kept)])
            except (OSError, ValueError):
                pass
            finally:
                try:
                    stream.close()
                except OSError:
                    pass
            return bytes(kept)

        out_box: list[bytes] = []
        err_box: list[bytes] = []
        readers = [
            threading.Thread(target=lambda: out_box.append(_drain(proc.stdout)), daemon=True),
            threading.Thread(target=lambda: err_box.append(_drain(proc.stderr)), daemon=True),
        ]
        for t in readers:
            t.start()
        try:
            try:
                code = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                # F1/WU-106: kill seluruh tree (bukan proc.kill() satu PID) --
                # anak-cucu tool recon tidak boleh jadi orphan saat timeout.
                kill_process_tree(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                for t in readers:
                    t.join(timeout=5)
                raise
            for t in readers:
                t.join(timeout=5)
        finally:
            get_running_actions().unregister(action_id)
        out = out_box[0].decode("utf-8", errors="replace") if out_box else ""
        err = err_box[0].decode("utf-8", errors="replace") if err_box else ""
        return out, err, code

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
            stdout_raw, stderr_raw, code = self._run_bounded(argv)
        except subprocess.TimeoutExpired:
            run.status = "timeout"
            run.error = f"timeout after {self.timeout}s"
            return
        except (OSError, ValueError) as exc:
            run.status = "failed"
            run.error = redact(str(exc))
            return

        # Parse the RAW stdout; redact only what lands on disk. Redacting first
        # fed mangled text to the parser -- the generic `key=value` pattern
        # consumes the rest of a whitespace-delimited token, including a closing
        # quote or comma -- which corrupted JSON artifacts and could collapse
        # two distinct URLs into one (a false dedupe). `_write_raw()` redacts,
        # so nothing unmasked is persisted on this path.
        raw = stdout_raw[:MAX_OUTPUT_BYTES]
        run.output_path = self._write_raw(spec.name, raw)
        run.normalized_items = adapter.parse(raw)
        run.status = "success" if code == 0 else "failed"
        if code != 0:
            run.error = redact(stderr_raw[:2000]) or f"exit {code}"

    def _run_api(self, spec: ToolSpec, target: str, run: ToolRun) -> None:
        from wth_ai.recon.tools.api_adapters import run_api_adapter

        items, err = run_api_adapter(spec.name, target)
        if err:
            run.status = "skipped"
            run.error = err
            return
        run.normalized_items = items
        run.status = "success" if items else "skipped"
        run.output_path = self._write_raw(spec.name, json.dumps(items, indent=2))
        if not items:
            run.error = "no results"

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
        write_runlog_entry(self.runlog_path, run)
