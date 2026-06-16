"""Engagement state machine — ported from legacy core."""

from __future__ import annotations

from enum import Enum


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
    LoopState.PLANNING: {LoopState.EXECUTING, LoopState.DONE},
    LoopState.EXECUTING: {LoopState.VERIFYING},
    LoopState.VERIFYING: {LoopState.REPLANNING, LoopState.REPORTING},
    LoopState.REPLANNING: {LoopState.PLANNING},
    LoopState.REPORTING: {LoopState.DONE},
}


class EngagementStateMachine:
    def __init__(self) -> None:
        self.state = LoopState.IDLE

    def transition(self, new: LoopState) -> bool:
        allowed = TRANSITIONS.get(self.state, set())
        if new not in allowed and new != LoopState.STOPPED:
            return False
        self.state = new
        return True
