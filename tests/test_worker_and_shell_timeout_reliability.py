"""Regression tests — WU-108: fix reliability bugs (process-leak-saat-timeout
di `shell_runner.py`, `WorkerEngine._run_job()` timeout kosmetik).

Bug 1 (`shell_runner.py::run_argv()`, jalur streaming/`on_line`): `for line
in proc.stdout:` blocking read TANPA timeout sendiri -- `proc.wait(timeout=
timeout)` di bawahnya cuma tercapai SETELAH loop itu selesai (yaitu SETELAH
proses sudah keluar). Proses yang hang tanpa pernah menutup stdout membuat
parameter `timeout` sama sekali tidak efektif -- loop blok SELAMANYA, proses
child leak (tidak pernah di-kill).
Fix: watchdog `threading.Timer` yang kill() proses setelah `timeout` detik
apa pun yang sedang terjadi di blocking read -- kill() menutup pipe, `for`
loop langsung lihat EOF dan berhenti alih-alih hang selamanya.

Bug 2 (`jobs/worker.py::WorkerEngine._run_job()`): `with
ThreadPoolExecutor(...) as ex:` -- `__exit__` memanggil `shutdown(wait=True)`
TANPA SYARAT. Saat `.result(timeout=...)` timeout, exception itu propagate
LEWAT `__exit__` blok `with`, yang BLOK sampai thread handler yang sudah
dianggap timeout benar-benar selesai sendiri (berapa lama pun) -- baru
setelah itu `except FutureTimeout:` sempat jalan. "Timeout kosmetik": status
job di-mark failed cepat, tapi WorkerEngine sendiri TIDAK benar-benar bisa
lanjut ke job berikutnya sampai handler asli selesai.
Fix: hapus `with`, panggil `ex.shutdown(wait=False)` eksplisit di semua
jalur -- pemanggil `_run_job()` lanjut segera, thread yang sudah telanjur
jalan jadi orphan di background (Python tidak bisa kill thread paksa --
membunuh PROSES nyata butuh RunningActionRegistry/WU-106, di luar scope
WU ini, tapi symptom "worker loop tidak bisa lanjut" tertutup).
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import contextlib
import tempfile
from pathlib import Path

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.worker import WorkerEngine
from wth_ai.shell_runner import run_command


@contextlib.contextmanager
def temp_home():
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        old_home = os.environ.get("WTH_HOME")
        old_cfg = cfg_mod.USER_CONFIG_PATH
        old_env = cfg_mod.USER_ENV_PATH
        os.environ["WTH_HOME"] = str(base)
        cfg_mod.USER_CONFIG_PATH = base / "config" / "user.yaml"
        cfg_mod.USER_ENV_PATH = base / ".env"
        init_workspace(base)
        try:
            yield base
        finally:
            cfg_mod.USER_CONFIG_PATH = old_cfg
            cfg_mod.USER_ENV_PATH = old_env
            if old_home is None:
                os.environ.pop("WTH_HOME", None)
            else:
                os.environ["WTH_HOME"] = old_home


# ---------------------------------------------------------------------------
# shell_runner.py -- proses hang tanpa menutup stdout benar-benar di-kill
# ---------------------------------------------------------------------------

def test_hung_process_killed_within_timeout_not_leaked_forever():
    """REGRESI-GUARD utama: sleep tidak pernah menulis/menutup stdout --
    sebelum fix, `for line in proc.stdout:` akan blok SELAMANYA (test ini
    sendiri akan hang/timeout kalau bug belum diperbaiki). timeout=2 harus
    benar-benar membatasi wall-clock, bukan cuma di-declare."""
    started = time.monotonic()
    stdout, stderr, code = run_command("sleep 30", timeout=2, on_line=lambda line: None)
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"proses hang tidak di-kill dalam batas wajar -- elapsed={elapsed:.1f}s"
    assert code == 124
    assert "timeout" in stderr.lower()


def test_normal_streaming_command_still_works_and_captures_output():
    """Compatibility: perintah normal (tidak hang) tetap berjalan seperti
    sebelumnya -- watchdog TIDAK boleh mengganggu proses yang selesai wajar."""
    lines: list[str] = []
    stdout, stderr, code = run_command(
        "printf 'line1\\nline2\\n'", timeout=10, on_line=lines.append,
    )
    assert code == 0
    assert lines == ["line1", "line2"]
    assert "timeout" not in stderr.lower()


def test_watchdog_cancelled_on_normal_completion_no_late_kill_side_effect():
    """Watchdog timer harus ter-cancel begitu proses selesai wajar --
    jalankan 2x berturut-turut cepat untuk pastikan tidak ada timer nyasar
    dari panggilan sebelumnya yang mengganggu panggilan berikutnya."""
    for _ in range(2):
        stdout, stderr, code = run_command("printf ok", timeout=5, on_line=lambda l: None)
        assert code == 0
        assert "timeout" not in stderr.lower()


# ---------------------------------------------------------------------------
# jobs/worker.py -- WorkerEngine tidak lagi blok pada handler yang timeout
# ---------------------------------------------------------------------------

def test_worker_returns_promptly_on_handler_timeout_not_blocked_by_with():
    """REGRESI-GUARD utama: handler tidur 5 detik, WorkerEngine.timeout=1 --
    _run_job() harus kembali dalam batas wajar SETELAH timeout tercapai
    (bukan menunggu 5 detik penuh sampai handler asli selesai sendiri)."""
    with temp_home():
        mgr = JobManager(WTHConfig(), bus=EventBus())
        worker = WorkerEngine(mgr, timeout=1)

        def _slow_handler(job, ctx):
            time.sleep(5)
            return {"ok": True}

        worker.register("custom", _slow_handler)
        job = mgr.create_job("custom")

        started = time.monotonic()
        result = worker.run_once()
        elapsed = time.monotonic() - started

        assert result["status"] == "failed"
        assert result["error"] == "timeout"
        assert elapsed < 4, (
            f"REGRESI: WorkerEngine._run_job() masih blok sampai handler asli "
            f"selesai (5s) alih-alih lanjut segera setelah timeout=1s -- elapsed={elapsed:.1f}s"
        )


def test_worker_normal_fast_handler_still_completes_successfully():
    """Compatibility: handler cepat (tidak timeout) tetap berjalan normal."""
    with temp_home():
        mgr = JobManager(WTHConfig(), bus=EventBus())
        worker = WorkerEngine(mgr, timeout=5)
        worker.register("custom", lambda job, ctx: {"ok": True})
        mgr.create_job("custom")
        result = worker.run_once()
        assert result["status"] == "completed"
