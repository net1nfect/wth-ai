"""Kill switch: proses anak mati < 2 detik, flag file untuk lintas-proses."""

import subprocess
import sys
import time

from wth_ai.killswitch import KillSwitch


def test_kill_stops_a_sleeping_child_in_under_two_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            start_new_session=True)
    ks = KillSwitch()
    ks.register("worker-1", proc.pid)
    started = time.monotonic()
    ks.activate()
    elapsed = time.monotonic() - started
    proc.wait(timeout=5)
    assert proc.poll() is not None
    assert elapsed < 2.0, f"kill butuh {elapsed:.2f}s"


def test_flag_file_makes_workers_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai import killswitch

    assert killswitch.requested() is False
    killswitch.request_kill("demo")
    assert killswitch.requested() is True
    killswitch.clear_kill()
    assert killswitch.requested() is False