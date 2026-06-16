"""Execution state machine with validation, history, and rollback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

from .models import utc_now


class ExecutionState(str, Enum):
    """Engagement execution states."""

    IDLE = "idle"
    INITIALIZING = "initializing"
    PHASE1_RECONNAISSANCE = "phase1_reconnaissance"
    PHASE2_RECONNAISSANCE = "phase2_reconnaissance"
    PHASE3_RECONNAISSANCE = "phase3_reconnaissance"
    PHASE4_RECONNAISSANCE = "phase4_reconnaissance"
    PHASE5_RECONNAISSANCE = "phase5_reconnaissance"
    PHASE6_RECONNAISSANCE = "phase6_reconnaissance"
    PHASE7_RECONNAISSANCE = "phase7_reconnaissance"
    PHASE8_RECONNAISSANCE = "phase8_reconnaissance"
    PHASE9_RECONNAISSANCE = "phase9_reconnaissance"
    PHASE10_RECONNAISSANCE = "phase10_reconnaissance"
    ANALYZING_RESULTS = "analyzing_results"
    TIER1_CLASSIFICATION = "tier1_classification"
    TIER2_PAYLOAD_GEN = "tier2_payload_gen"
    TIER3_EXECUTION = "tier3_execution"
    TIER4_ESCALATION = "tier4_escalation"
    LATERAL_MOVEMENT = "lateral_movement"
    DATA_EXTRACTION = "data_extraction"
    REPORTING = "reporting"
    CLEANUP = "cleanup"
    ERROR = "error"


@dataclass(slots=True)
class StateTransition:
    """Recorded state transition."""

    previous: ExecutionState
    current: ExecutionState
    timestamp: str
    reason: str


class ExecutionStateMachine:
    """Validate and record engagement state transitions."""

    def __init__(self) -> None:
        self.current = ExecutionState.IDLE
        self.history: list[StateTransition] = []
        self._lock = asyncio.Lock()

    def _allowed(self, target: ExecutionState) -> bool:
        if target in {ExecutionState.ERROR, ExecutionState.CLEANUP}:
            return True
        if self.current == ExecutionState.ERROR:
            return target in {ExecutionState.INITIALIZING, ExecutionState.CLEANUP}
        if self.current == ExecutionState.CLEANUP:
            return target == ExecutionState.IDLE
        ordered = list(ExecutionState)
        return ordered.index(target) >= ordered.index(self.current)

    async def transition(self, target: ExecutionState, reason: str = "") -> None:
        """Move to a valid target state."""
        async with self._lock:
            if not self._allowed(target):
                raise ValueError(f"Invalid transition: {self.current.value} -> {target.value}")
            previous = self.current
            self.current = target
            self.history.append(StateTransition(previous, target, utc_now(), reason))

    async def rollback(self) -> ExecutionState:
        """Rollback to the previous known state."""
        async with self._lock:
            if not self.history:
                return self.current
            transition = self.history.pop()
            self.current = transition.previous
            return self.current

    async def transition_with_timeout(
        self, target: ExecutionState, timeout: float, reason: str = ""
    ) -> None:
        """Transition with timeout enforcement."""
        await asyncio.wait_for(self.transition(target, reason), timeout)
