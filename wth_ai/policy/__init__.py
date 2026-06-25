"""Policy package: execution-mode control plane (Fase 6, Stage 1 / C11).

This complements the job-admission ``PolicyEngine`` (``wth_ai/jobs/policy.py``)
with an explicit *execution-mode router* that bounds what an engagement is
allowed to do (passive vs active vs authenticated vs mutating), independent of
which tools are installed. Every active capability stays gated and auditable.
"""

from wth_ai.policy.execution_mode import (
    EXECUTION_MODES,
    ModeCapability,
    capability,
    default_mode_for_profile,
    mode_allows_risk,
    resolve_mode,
)

__all__ = [
    "EXECUTION_MODES",
    "ModeCapability",
    "capability",
    "default_mode_for_profile",
    "mode_allows_risk",
    "resolve_mode",
]
