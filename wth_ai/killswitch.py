"""Kill switch: sinyal proses + flag file untuk worker lintas-proses.

Dua jalur karena satu proses tidak bisa melihat registry proses lain:

* :meth:`KillSwitch.activate` — SIGTERM ke process group, SIGKILL setelah
  grace, untuk worker yang hidup di proses ini (TUI, loop).
* :func:`request_kill` — menulis flag file yang dipoll :func:`requested` oleh
  worker di proses lain (`wth-ai worker run`), sehingga `wth-ai kill` tetap bekerja
  tanpa IPC.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from wth_ai.audit import audit_log
from wth_ai.workspace import wth_home


def kill_flag() -> Path:
    return wth_home() / "runtime" / "KILL"


def request_kill(reason: str = "operator") -> Path:
    path = kill_flag()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason + "\n", encoding="utf-8")
    audit_log("KILL_REQUESTED", reason=reason)
    return path


def requested() -> bool:
    return kill_flag().exists()


def clear_kill() -> None:
    try:
        kill_flag().unlink()
    except FileNotFoundError:
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class KillSwitch:
    grace_seconds: float = 1.0
    _event: threading.Event = field(default_factory=threading.Event)
    _pids: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, name: str, pid: int) -> None:
        with self._lock:
            self._pids[name] = pid

    def unregister(self, name: str) -> None:
        with self._lock:
            self._pids.pop(name, None)

    def killed(self) -> bool:
        return self._event.is_set() or requested()

    def activate(self) -> list[str]:
        self._event.set()
        with self._lock:
            pids = dict(self._pids)
        stopped: list[str] = []
        for name, pid in pids.items():
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                stopped.append(name)
            except (ProcessLookupError, PermissionError):
                continue
        deadline = time.monotonic() + self.grace_seconds
        while time.monotonic() < deadline:
            if not any(_alive(pid) for pid in pids.values()):
                break
            time.sleep(0.02)
        for pid in pids.values():
            if _alive(pid):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        with self._lock:
            self._pids.clear()
        self._event.clear()
        audit_log("KILL_SWITCH", stopped=stopped)
        return stopped


KILL_SWITCH = KillSwitch()