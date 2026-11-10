"""Safe subprocess runner — argv list only, no shell=True."""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
from typing import Callable

from wth_ai.redaction import redact_secrets

MAX_OUTPUT_BYTES = 512_000


def redact_output(text: str) -> str:
    """Redaksi keluaran subprocess memakai redaktor kanonik.

    Pola lokal lama (api_key|token|password|secret|authorization) melewatkan
    bentuk seperti `aws_secret_access_key=` dan JWT, sehingga dua jalur redaksi
    berbeda ada di repo. Sekarang satu sumber: `redaction.redact_secrets`.
    """
    return redact_secrets(text or "")


def tokenize_command(command: str) -> list[str]:
    """Split a command string into argv without invoking a shell."""
    command = command.strip()
    if not command:
        return []
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return command.split()


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
            )
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            assert proc.stdout is not None
            assert proc.stderr is not None

            # stderr dibaca di thread terpisah. Tanpa ini, anak yang menulis
            # >64KB ke stderr memblokir saat pipe penuh sementara induk masih
            # membaca stdout dan belum memanggil wait() -> deadlock sampai timeout.
            def _drain_stderr() -> None:
                for raw in proc.stderr:  # type: ignore[union-attr]
                    stderr_lines.append(raw.rstrip("\n"))

            drain = threading.Thread(target=_drain_stderr, name="wth-stderr", daemon=True)
            drain.start()

            # Timeout dijaga thread sendiri. `proc.wait(timeout=...)` tidak bisa
            # dipakai di sini: ia baru dievaluasi setelah loop stdout selesai,
            # dan loop itu menunggu EOF -- yang baru datang saat anak mati.
            timed_out = threading.Event()

            def _enforce_timeout() -> None:
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out.set()
                    _terminate(proc)

            watchdog = threading.Thread(target=_enforce_timeout, name="wth-timeout", daemon=True)
            watchdog.start()
            try:
                for raw in proc.stdout:
                    line = raw.rstrip("\n")
                    stdout_lines.append(line)
                    # Redaksi SEBELUM on_line: callback operator (TUI, log live)
                    # adalah permukaan yang bisa dipersistensikan.
                    on_line(redact_secrets(line))
            finally:
                watchdog.join(timeout=10.0)
                drain.join(timeout=5.0)
            if timed_out.is_set():
                return "", f"timeout after {timeout}s", 124
            stdout = redact_output("\n".join(stdout_lines))[:max_bytes]
            return stdout, redact_output("\n".join(stderr_lines))[:max_bytes], proc.returncode or 0
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=base_env,
        )
        return (
            redact_output(proc.stdout or "")[:max_bytes],
            redact_output(proc.stderr or "")[:max_bytes],
            proc.returncode,
        )
    except subprocess.TimeoutExpired:
        # Jalur streaming sudah memanggil _terminate(proc) sebelum kembali.
        # subprocess.run membunuh anaknya sendiri.
        return "", f"timeout after {timeout}s", 124
    except OSError as exc:
        return "", str(exc), 1


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Bunuh proses anak dan tunggu, supaya timeout tidak meninggalkan zombie."""
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


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
