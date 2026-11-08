"""Coverage: `wth_ai/cron/ticker.py` (LocalCronTicker).

Ticker adalah thread latar yang men-drain antrean job. Ini memverifikasi
lifecycle (start/stop) dan `tick_once` dengan scheduler/store asli — tanpa
jaringan, dan tanpa menunggu interval (interval 0.01 detik).
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


def _ticker():
    from wth_ai.cron.ticker import LocalCronTicker

    return LocalCronTicker(interval=0.01)


def test_tick_once_empty_returns_empty_list():
    t = _ticker()
    assert t.tick_once() == []
    t.stop()


def test_start_launches_thread_and_stop_halts():
    t = _ticker()
    assert t._thread is None
    t.start()
    assert t._running is True
    assert t._thread is not None
    # stop tidak menggabung thread; hanya mematikan bendera.
    t.stop()
    assert t._running is False


def test_start_idempotent():
    t = _ticker()
    t.start()
    thread = t._thread
    t.start()  # kedua: tidak boleh membuat thread baru
    assert t._thread is thread
    t.stop()


def test_loop_survives_exception_from_scheduler():
    """Loop menelan pengecualian (baris 47) supaya thread tidak mati."""
    t = _ticker()

    class _BoomScheduler:
        def tick(self):
            raise RuntimeError("boom")

    t.scheduler = _BoomScheduler()
    t.start()
    time.sleep(0.05)
    assert t._running is True
    t.stop()
