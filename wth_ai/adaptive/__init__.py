"""Adaptive response-driven testing: signals, mutations, memory, campaigns."""

from __future__ import annotations

from .engine import AdaptiveAttempt, AdaptiveEngine, AdaptiveReport, Observation, format_report
from .memory import TargetMemory
from .request import HttpRequest
from .signals import Signal, classify, looks_blocked
from .strategies import CATALOG, Move, Strategy, propose

__all__ = [
    "AdaptiveAttempt", "AdaptiveEngine", "AdaptiveReport", "Observation", "format_report",
    "TargetMemory", "HttpRequest", "Signal", "classify", "looks_blocked",
    "CATALOG", "Move", "Strategy", "propose",
]
