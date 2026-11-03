"""Loop state machine — enforcement-by-construction (WU-006, ADR-011, PRD-008).

Renamed from `EngagementStateMachine` to `LoopStateMachine` (ADR-011 SS Naming
Collision) to avoid colliding with the separate, security-critical
`Engagement` authorization entity (PRD-003) — this class governs in-process,
single-instance WORKFLOW phase (`EngagementLoop`), a completely different
risk profile from `Engagement.status` (cross-process authorization
lifecycle, enforced independently by `ExecutionBroker` fetch-fresh, WU-101+).

Previously `TRANSITIONS` + `.transition()` were correctly defined but never
enforced structurally: `state` was a plain public attribute, so
`engagement_loop.py` wrote `self.sm.state = LoopState.X` directly at 6 call
sites, bypassing the guard entirely — `.transition()` was never actually
called anywhere in the codebase before this WU (write-only/vestigial).
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any


class LoopState(str, Enum):
    IDLE = "idle"
    RECON = "recon"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    REPORTING = "reporting"
    DONE = "done"
    STOPPED = "stopped"


TRANSITIONS = {
    LoopState.IDLE: {LoopState.RECON},
    LoopState.RECON: {LoopState.PLANNING},
    # PLANNING->REPORTING: planner produced zero tasks this iteration ->
    # EngagementLoop breaks early and reports with whatever exists so far
    # (engagement_loop.py: `if not tasks: ... break`). Discovered via WU-006
    # trace-before-enforce -- the original table (authored while
    # `.transition()` was still dead code, never exercised against real
    # control flow) didn't have this edge; a real passing E2E test
    # (tests/test_hunt_pipeline_e2e.py) exercises a variant of this
    # early-report path, so this is a correction, not new behavior.
    LoopState.PLANNING: {LoopState.EXECUTING, LoopState.DONE, LoopState.REPORTING},
    LoopState.EXECUTING: {LoopState.VERIFYING},
    LoopState.VERIFYING: {LoopState.REPLANNING, LoopState.REPORTING},
    # REPLANNING->REPORTING: normal loop exhaustion (max_iterations reached
    # without a high/critical verified finding forcing early exit) -- the
    # NORMAL termination path, not a bug. Same discovery as above.
    LoopState.REPLANNING: {LoopState.PLANNING, LoopState.REPORTING},
    LoopState.REPORTING: {LoopState.DONE},
}


class InvalidTransitionError(Exception):
    """Raised by `LoopStateMachine.transition()` for an illegal transition
    when running under WTH_TEST_MODE=1 or WTH_STRICT_SM=1 (PRD-008)."""


class LoopStateMachine:
    """`state` is read-only (property, no setter) -- `transition()` is the
    SOLE mutator. `sm.state = X` now raises `AttributeError` instead of
    silently succeeding (PRD-008 acceptance criterion), which is exactly the
    bypass that made the old `EngagementStateMachine` vestigial."""

    def __init__(self, *, bus: Any = None) -> None:
        self._state = LoopState.IDLE
        self.bus = bus

    @property
    def state(self) -> LoopState:
        return self._state

    def transition(self, new: LoopState) -> bool:
        """Attempt a transition. Returns True/False; raises
        `InvalidTransitionError` for an illegal transition under
        WTH_TEST_MODE=1/WTH_STRICT_SM=1 so tests fail loudly instead of the
        bug silently passing (matches the class's whole reason for existing).
        Outside those modes: clamp -- state is left unchanged, not corrupted,
        not pretended-successful."""
        allowed = TRANSITIONS.get(self._state, set())
        if new not in allowed and new != LoopState.STOPPED:
            self._emit_invalid(new)
            if os.environ.get("WTH_TEST_MODE") == "1" or os.environ.get("WTH_STRICT_SM") == "1":
                raise InvalidTransitionError(
                    f"Illegal loop state transition: {self._state.value} -> {new.value}"
                )
            return False
        self._state = new
        return True

    def _emit_invalid(self, attempted: LoopState) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(
                "invalid_state_transition",
                source="loop_state_machine",
                data={"from": self._state.value, "to": attempted.value},
            )
        except Exception:
            pass
