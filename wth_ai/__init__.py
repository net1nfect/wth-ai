"""WTH-AI — Dual-agent cybersecurity research framework."""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "1.0.0"
__all__ = ["WTHOrchestrator", "__version__"]


def __getattr__(name: str):
    if name == "WTHOrchestrator":
        from wth_ai.orchestrator import WTHOrchestrator
        return WTHOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    from wth_ai.orchestrator import WTHOrchestrator
