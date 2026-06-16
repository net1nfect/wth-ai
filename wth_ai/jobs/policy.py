"""Minimal policy engine for job admission (Fase 4).

Non-destructive, safe-by-default. Rejects out-of-policy jobs before they enter
the queue. Designed to be extended with richer scope/rate rules later.
"""

from __future__ import annotations

from typing import Any

from wth_ai.jobs.model import JOB_TYPES

# Job types that operate against a target and therefore require one.
_TARGET_REQUIRED: frozenset[str] = frozenset({"recon", "executor"})


class PolicyEngine:
    def __init__(self, config: Any = None) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        type: str,
        target: str | None = None,
        forbidden: tuple[str, ...] = (),
        safe_mode: bool = True,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). reason is empty when allowed."""
        if type not in JOB_TYPES:
            return False, f"unknown job type '{type}'"
        if type in _TARGET_REQUIRED and not (target and target.strip()):
            return False, f"job type '{type}' requires a target"
        if target:
            for pat in forbidden:
                if pat and pat.lower() in target.lower():
                    return False, f"target matches forbidden pattern '{pat}'"
        # Safe mode is always enforced; destructive actions are out of scope.
        if not safe_mode:
            return False, "safe_mode disabled is not permitted"
        return True, ""
