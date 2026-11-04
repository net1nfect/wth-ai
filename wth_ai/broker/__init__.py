"""Execution broker package (WU-102, PRD-011).

The broker is the single seam between "an agent decided to act" and "a tool
ran". Today it is a thin adapter over :class:`AgentTerminalBridge` so the call
sites (`dispatch_tool`) keep one entry point while the intent/result types give
future layers -- claim linking, running-action registry, kill switch -- a
stable place to attach.
"""
from __future__ import annotations

from wth_ai.broker.broker import ExecutionBroker
from wth_ai.broker.models import ActionIntent, ExecutionResult

__all__ = ["ActionIntent", "ExecutionBroker", "ExecutionResult"]
