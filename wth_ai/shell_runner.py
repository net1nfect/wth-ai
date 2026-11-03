"""Safe subprocess runner — argv list only, no shell=True."""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading
from typing import Callable

from wth_ai.running_actions import get_running_actions

MAX_OUTPUT_BYTES = 512_000

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"), r"\1=***REDACTED***"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer ***REDACTED***"),
)


def redact_output(text: str) -> str:
    out = text or ""
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def tokenize_command(command: str) -> list[str]:
    """Split a command string into argv without invoking a shell."""
    command = command.strip()
    if not command:
        return []
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return command.split()


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """SIGKILL seluruh process group `proc` (anak-cucu ikut), fallback `proc.kill()`.

    Valid karena kedua jalur spawn memakai `start_new_session=True` — anak jadi
    leader group-nya sendiri (`pgid == pid`). Membunuh satu PID saja meninggalkan
    anak-cucunya hidup (kelas bug sama dengan pipe-timeout WU-108).
    """
    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_argv(
    argv: list[str],
    *,
    timeout: int = 120,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    max_bytes: int = MAX_OUTPUT_BYTES,
    on_line: Callable[[str], None] | None = None,
) -> tuple[str, str, int]:
    """Run argv list safely. Returns (stdout, stderr, exit_code)."""
    if not argv:
        return "", "empty command", 1
    base_env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    if env:
        base_env.update(env)
    try:
        if on_line is not None:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                env=base_env,
                # F1/WU-106: sesi sendiri -> pgid == pid, supaya kill-switch dan
                # kill-tree saat timeout menjangkau SELURUH proses tree, bukan
                # cuma proses langsung.
                start_new_session=(os.name != "nt"),
            )
            action_id = get_running_actions().register(proc.pid, label=" ".join(argv[:3])[:100])
            # WU-108: `for line in proc.stdout:` blocks reading until the
            # pipe hits EOF (process exits) -- it has NO timeout of its own,
            # so `timeout` was completely ineffective for a hung process that
            # never closes stdout (proc.wait(timeout=...) below is only ever
            # reached AFTER that loop already finished, i.e. after the
            # process already exited). A watchdog timer kills the process
            # after `timeout` seconds regardless of what the blocking read
            # is doing -- killing it closes the pipe, which makes the `for`
            # loop see EOF and return promptly instead of hanging forever.
            timed_out = threading.Event()

            def _kill_on_timeout() -> None:
                timed_out.set()
                _kill_process_tree(proc)

            watchdog = threading.Timer(timeout, _kill_on_timeout)
            watchdog.daemon = True
            watchdog.start()
            try:
                stdout_lines: list[str] = []
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    stdout_lines.append(line)
                    on_line(line)
                proc.wait(timeout=timeout)
            finally:
                watchdog.cancel()
                get_running_actions().unregister(action_id)
            if timed_out.is_set():
                return "", f"timeout after {timeout}s", 124
            stderr = (proc.stderr.read() if proc.stderr else "") or ""
            stdout = redact_output("\n".join(stdout_lines))[:max_bytes]
            return stdout, redact_output(stderr)[:max_bytes], proc.returncode or 0
        # Popen (bukan subprocess.run) supaya PID/process-group tercatat ke
        # RunningActionRegistry: proses non-streaming juga harus bisa dibunuh
        # kill-switch dan tidak meninggalkan anak saat timeout.
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=base_env,
            start_new_session=(os.name != "nt"),
        )
        action_id = get_running_actions().register(proc.pid, label=" ".join(argv[:3])[:100])
        try:
            try:
                stdout_raw, stderr_raw = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_process_tree(proc)
                proc.communicate()
                return "", f"timeout after {timeout}s", 124
            return (
                redact_output(stdout_raw or "")[:max_bytes],
                redact_output(stderr_raw or "")[:max_bytes],
                proc.returncode or 0,
            )
        finally:
            get_running_actions().unregister(action_id)
    except subprocess.TimeoutExpired:
        return "", f"timeout after {timeout}s", 124
    except OSError as exc:
        return "", str(exc), 1


def run_command(
    command: str,
    *,
    timeout: int = 120,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    max_bytes: int = MAX_OUTPUT_BYTES,
    on_line: Callable[[str], None] | None = None,
) -> tuple[str, str, int]:
    """Parse command to argv and run without shell."""
    return run_argv(
        tokenize_command(command),
        timeout=timeout,
        cwd=cwd,
        env=env,
        max_bytes=max_bytes,
        on_line=on_line,
    )
