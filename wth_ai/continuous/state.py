"""Persistent state for the proactive autonomy daemon."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]


def runtime_dir() -> Path:
    try:
        from wth_ai.workspace import wth_home

        base = wth_home() / "runtime"
    except Exception:  # noqa: BLE001
        base = Path.home() / ".wth-ai" / "runtime"
    base.mkdir(parents=True, exist_ok=True)
    return base


def state_path() -> Path:
    return runtime_dir() / "autonomy.json"


def pid_path() -> Path:
    return runtime_dir() / "autonomy.pid"


def log_path() -> Path:
    return runtime_dir() / "autonomy.log"


@contextmanager
def _locked() -> Iterator[None]:
    lock_file = runtime_dir() / "autonomy.lock"
    with open(lock_file, "a+") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@dataclass
class AutonomyState:
    targets: list[str] = field(default_factory=list)
    interval: int = 300
    steps: int = 6
    running: bool = False
    pid: int = 0
    started_at: float = 0.0
    last_cycle: float = 0.0
    cycles: int = 0
    steps_run: int = 0
    adaptive_steps: int = 0
    adaptive_wins: int = 0
    last_target: str = ""
    last_error: str = ""
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": self.targets, "interval": self.interval, "steps": self.steps,
            "running": self.running, "pid": self.pid, "started_at": self.started_at,
            "last_cycle": self.last_cycle, "cycles": self.cycles,
            "steps_run": self.steps_run, "adaptive_steps": self.adaptive_steps,
            "adaptive_wins": self.adaptive_wins, "last_target": self.last_target,
            "last_error": self.last_error, "log": self.log[-80:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutonomyState":
        return cls(
            targets=[str(t) for t in (data.get("targets") or [])],
            interval=int(data.get("interval", 300) or 300),
            steps=int(data.get("steps", 6) or 6),
            running=bool(data.get("running", False)),
            pid=int(data.get("pid", 0) or 0),
            started_at=float(data.get("started_at", 0) or 0),
            last_cycle=float(data.get("last_cycle", 0) or 0),
            cycles=int(data.get("cycles", 0) or 0),
            steps_run=int(data.get("steps_run", 0) or 0),
            adaptive_steps=int(data.get("adaptive_steps", 0) or 0),
            adaptive_wins=int(data.get("adaptive_wins", 0) or 0),
            last_target=str(data.get("last_target", "") or ""),
            last_error=str(data.get("last_error", "") or ""),
            log=[str(x) for x in (data.get("log") or [])],
        )


def read_state() -> AutonomyState:
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    state = AutonomyState.from_dict(data)
    if state.pid and not pid_alive(state.pid):
        state.running = False
    return state


def _write_unlocked(state: AutonomyState) -> None:
    path = state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    if state.pid:
        pid_path().write_text(str(state.pid), encoding="utf-8")
    else:
        try:
            pid_path().unlink()
        except OSError:
            pass


def write_state(state: AutonomyState) -> None:
    with _locked():
        _write_unlocked(state)


def update_state(**changes: Any) -> AutonomyState:
    with _locked():
        state = read_state()
        for key, value in changes.items():
            if hasattr(state, key):
                setattr(state, key, value)
        _write_unlocked(state)
        return state


def log_line(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    state = read_state()
    state.log.append(line)
    state.log = state.log[-100:]
    write_state(state)


def clear_pid() -> None:
    try:
        pid_path().unlink()
    except OSError:
        pass
