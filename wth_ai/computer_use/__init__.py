"""Computer use — real desktop control for the agent (Hermes-style "use computer")."""

from __future__ import annotations

from wth_ai.computer_use.session import (
    INPUT_ACTIONS,
    READ_ONLY_ACTIONS,
    ActionResult,
    ComputerUse,
)

__all__ = ["ComputerUse", "ActionResult", "READ_ONLY_ACTIONS", "INPUT_ACTIONS"]
