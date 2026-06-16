"""Core services for H1T4M-AI."""

from typing import Any

from .config import H1T4MConfiguration

__all__ = ["H1T4MConfiguration", "H1T4M_Orchestrator"]


def __getattr__(name: str) -> Any:
    """Lazily expose the orchestrator without creating agent import cycles."""
    if name == "H1T4M_Orchestrator":
        from .orchestrator import H1T4M_Orchestrator

        return H1T4M_Orchestrator
    raise AttributeError(name)
