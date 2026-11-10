"""run_argv: redaksi di jalur streaming, stderr tidak deadlock, timeout membunuh.

Item 5: di jalur `on_line`, `on_line(line)` dipanggil SEBELUM `redact_output`,
stderr dibaca setelah `proc.wait()` (deadlock bila penuh), dan `TimeoutExpired`
tidak membunuh proses.
"""

from __future__ import annotations

import os
import sys

import pytest

from wth_ai.redaction import MASKED
from wth_ai.shell_runner import run_argv

#: Nilai yang harus tertangkap redaktor kanonik. Pola lokal lama (api_key|
#: token|password|secret|authorization) tidak mengenali bentuk ini.
AWS_SECRET = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)

def _run(script: str, **kwargs):
    return run_argv([sys.executable, "-c", script], **kwargs)

def test_streamed_lines_are_redacted_before_on_line():
    """on_line menerima baris yang sudah diredaksi, bukan bahan mentah."""
    seen: list[str] = []
    stdout, stderr, code = _run(
        f"print({AWS_SECRET!r}); print({JWT!r})",
        on_line=seen.append,
        timeout=30,
    )

    assert code == 0, stderr
    assert seen, "on_line tidak dipanggil"
    joined = "\n".join(seen) + stdout
    assert "wJalrXUtnFEMI" not in joined, f"secret AWS lolos ke on_line: {seen}"
    assert "dozjgNryP4J3jVmNHl0w5N" not in joined, f"JWT lolos ke on_line: {seen}"
    assert MASKED in joined

def test_secret_on_stderr_is_redacted():
    """stderr juga diredaksi; nilai tidak boleh selamat ke pemanggil."""
    stdout, stderr, code = _run(
        f"import sys; print({AWS_SECRET!r}, file=sys.stderr)",
        timeout=30,
    )
    assert code == 0
    assert "wJalrXUtnFEMI" not in (stdout + stderr)

def test_large_stderr_does_not_deadlock():
    """200KB ke stderr: pipe penuh tidak boleh menggantung proses.

    Tanpa pembaca stderr paralel, pipe 64KB penuh dan anak memblokir di write
    sementara induk menunggu stdout EOF. Test menulis stderr DULU lalu dengan
    sengaja hidup sebentar, supaya kondisi pipe-penuh benar-benar terjadi
    (anak yang langsung mati bisa lolos tanpa memperbaiki apa pun).
    """
    script = (
        "import sys, time\n"
        "sys.stderr.write('E' * 200_000); sys.stderr.flush()\n"
        "time.sleep(1.0)\n"
        "print('done', flush=True)\n"
    )
    stdout, stderr, code = _run(script, timeout=20)

    assert code == 0, f"dipanggil dengan kode {code}"
    assert "done" in stdout or "done" in stderr
    assert len(stdout) + len(stderr) >= 200_000

def test_timeout_kills_child_process():
    """Timeout mengembalikan 124 dan proses anak benar-benar mati."""
    script = (
        "import os, sys, time\n"
        "print(os.getpid(), flush=True)\n"
        "time.sleep(60)\n"
    )
    child_pid: list[int] = []

    def on_line(line: str) -> None:
        try:
            child_pid.append(int(line.strip()))
        except ValueError:
            pass

    stdout, _stderr, code = _run(script, timeout=2, on_line=on_line)

    assert code == 124
    assert child_pid, f"pid anak tidak terbaca dari stdout: {stdout!r}"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid[0], 0)
