"""Background proactive autonomy daemon.

Run with: python -m wth_ai.continuous.daemon --interval 300 --steps 6 --target host
The parent CLI (`wth autonomy start`) spawns this module and records the PID.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from .runner import run_cycle, summarize
from .state import (
    AutonomyState, log_line, read_state, state_path, update_state, write_state,
)

_STOP = False


def _handle_signal(signum, frame) -> None:  # noqa: ARG001
    global _STOP
    _STOP = True


def _load_orchestrator(config_path: str):
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator
    from wth_ai.workspace import init_workspace

    init_workspace()
    cfg = WTHConfig.load(config_path)
    return WTHOrchestrator(cfg, fresh=True)


def loop(*, interval: int, steps: int, cycles: int, config_path: str) -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    state = read_state()
    if not state.targets:
        log_line("daemon: no targets configured; exiting")
        return 2

    log_line(f"daemon: start pid={os.getpid()} targets={state.targets} interval={state.interval}s")
    update_state(running=True, pid=os.getpid(), started_at=time.time(), last_error="")

    try:
        orch = _load_orchestrator(config_path)
    except Exception as exc:  # noqa: BLE001
        log_line(f"daemon: orchestrator init failed: {type(exc).__name__}: {exc}")
        update_state(running=False, pid=0, last_error=str(exc))
        return 1

    while not _STOP:
        current = read_state()
        targets = list(current.targets)
        if not targets:
            log_line("daemon: target list became empty; idling")
        else:
            try:
                summary = run_cycle(
                    orch, targets,
                    steps=steps or current.steps,
                    cycles=cycles,
                    adaptive=True,
                )
                steps_run = sum(int(v.get("steps", 0)) for v in summary.get("targets", {}).values())
                adaptive_steps = sum(int(v.get("adaptive_steps", 0)) for v in summary.get("targets", {}).values())
                adaptive_wins = sum(int(v.get("adaptive_wins", 0)) for v in summary.get("targets", {}).values())
                update_state(
                    running=True, pid=os.getpid(),
                    cycles=current.cycles + 1,
                    steps_run=current.steps_run + steps_run,
                    adaptive_steps=current.adaptive_steps + adaptive_steps,
                    adaptive_wins=current.adaptive_wins + adaptive_wins,
                    last_cycle=time.time(),
                    last_target=targets[-1],
                    last_error="",
                )
                log_line(f"daemon: cycle {current.cycles + 1} — {summarize(summary)}")
            except Exception as exc:  # noqa: BLE001
                log_line(f"daemon: cycle error: {type(exc).__name__}: {exc}")
                update_state(running=True, pid=os.getpid(), last_error=str(exc))

        deadline = time.time() + max(1, interval or current.interval)
        while not _STOP and time.time() < deadline:
            time.sleep(1)

    log_line("daemon: stop requested")
    update_state(running=False, pid=0)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WTH-AI proactive autonomy daemon")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--config", dest="config_path", default="config/wth_config.yaml")
    parser.add_argument("--target", action="append", default=[])
    args = parser.parse_args(argv)

    if args.target:
        state = read_state()
        merged = list(dict.fromkeys([*state.targets, *args.target]))
        state.targets = merged
        state.interval = args.interval
        state.steps = args.steps
        write_state(state)
    return loop(interval=args.interval, steps=args.steps, cycles=args.cycles,
                config_path=args.config_path)


if __name__ == "__main__":
    raise SystemExit(main())
