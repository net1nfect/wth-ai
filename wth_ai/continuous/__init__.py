"""Proactive autonomy: persistent runner, state, daemon control."""

from __future__ import annotations

from .control import add_targets, format_status, remove_targets, start, status, stop
from .runner import run_cycle, summarize
from .state import AutonomyState, read_state

__all__ = [
    "AutonomyState", "read_state", "run_cycle", "summarize",
    "start", "stop", "status", "add_targets", "remove_targets", "format_status",
]
