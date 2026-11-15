"""Start/stop/status control for the proactive autonomy daemon."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .state import (
    AutonomyState, clear_pid, log_path, log_line, pid_alive, read_state, update_state,
    write_state,
)


def _repo_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / "wth_ai").is_dir() else None


def add_targets(targets: list[str]) -> AutonomyState:
    state = read_state()
    state.targets = list(dict.fromkeys([*state.targets, *[t.strip() for t in targets if t.strip()]]))
    write_state(state)
    return state


def remove_targets(targets: list[str]) -> AutonomyState:
    state = read_state()
    drop = {t.strip() for t in targets if t.strip()}
    state.targets = [t for t in state.targets if t not in drop]
    write_state(state)
    return state


def start(
    targets: list[str] | None = None,
    *,
    interval: int = 300,
    steps: int = 6,
    cycles: int = 1,
    no_limits: bool = True,
) -> AutonomyState:
    state = read_state()
    if state.running and pid_alive(state.pid):
        return state
    if targets:
        state.targets = list(dict.fromkeys([*state.targets, *[t.strip() for t in targets if t.strip()]]))
    if not state.targets:
        raise ValueError("tidak ada target; tambahkan dengan `wth autonomy add <host>`")
    state.interval = max(10, int(interval))
    state.steps = max(1, int(steps))
    state.running = True
    state.started_at = time.time()
    state.last_error = ""
    write_state(state)

    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["WTH_NO_LIMITS"] = "1" if no_limits else "0"
    root = _repo_root()
    if root is not None:
        env["PYTHONPATH"] = f"{root}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)

    cmd = [
        sys.executable, "-m", "wth_ai.continuous.daemon",
        "--interval", str(state.interval),
        "--steps", str(state.steps),
        "--cycles", str(max(1, int(cycles))),
    ]
    for target in state.targets:
        cmd += ["--target", target]

    try:
        with open(log, "a", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(root) if root else None,
                env=env,
                start_new_session=True,
            )
    except Exception as exc:  # noqa: BLE001
        state.running = False
        state.pid = 0
        state.last_error = f"{type(exc).__name__}: {exc}"
        write_state(state)
        raise
    state.pid = proc.pid
    write_state(state)
    time.sleep(0.5)
    code = proc.poll()
    if code is not None:
        tail = ""
        try:
            tail = "\n".join(log.read_text(encoding="utf-8").splitlines()[-12:])
        except OSError:
            pass
        state.running = False
        state.pid = 0
        state.last_error = tail or f"daemon exited with code {code}"
        write_state(state)
        raise RuntimeError(state.last_error)
    log_line(f"control: started pid={proc.pid} targets={state.targets} interval={state.interval}s")
    return state


def stop() -> AutonomyState:
    state = read_state()
    pid = state.pid
    if pid and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + 6
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.2)
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    state.running = False
    state.pid = 0
    write_state(state)
    clear_pid()
    log_line("control: stopped")
    return state


def status() -> AutonomyState:
    return read_state()


def format_status(state: AutonomyState) -> str:
    if state.running and pid_alive(state.pid):
        head = f"● RUNNING pid={state.pid}"
    else:
        head = "○ idle"
    lines = [
        f"## Autonomy — {head}",
        f"targets: {', '.join(state.targets) if state.targets else '(none)'}",
        f"interval: {state.interval}s · steps/target: {state.steps}",
        f"cycles: {state.cycles} · steps: {state.steps_run} · "
        f"adaptive: {state.adaptive_steps} ({state.adaptive_wins} win)",
        f"last cycle: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state.last_cycle)) if state.last_cycle else '-'}"
        f" · last target: {state.last_target or '-'}",
    ]
    if state.last_error:
        lines.append(f"last error: {state.last_error[:400]}")
    if state.log:
        lines.append("")
        lines.append("recent:")
        lines.extend(f"  {line}" for line in state.log[-8:])
    lines.append("")
    lines.append("`wth autonomy start|stop|status|add <host>|rm <host>|run`")
    return "\n".join(lines)
