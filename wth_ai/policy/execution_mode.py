"""Execution-mode router (flowchart node C11).

An *execution mode* is the safety envelope for an engagement. It is orthogonal
to the recon ``profile`` (which controls *how many* tools run) — the mode
controls *what kind* of actions are permitted at all:

- ``passive-only``       : OSINT / passive collection only, no requests to target.
- ``safe-active``        : non-destructive active enumeration + safe probing.
- ``authenticated-safe`` : adds credentialed reads (sessions/tokens), still no mutation.
- ``approval-required``  : adds state-changing / sensitive actions, each human-gated.
- ``report-only``        : no network at all; (re)build reports from stored evidence.
- ``recovery``           : degraded mode after an error; passive + audit only.

The router never *executes* anything; it answers capability questions that the
job admission engine, planner, and executor consult before acting.
"""

from __future__ import annotations

from dataclasses import dataclass

EXECUTION_MODES: tuple[str, ...] = (
    "passive-only",
    "safe-active",
    "authenticated-safe",
    "approval-required",
    "report-only",
    "recovery",
)

_RISK_RANK: dict[str, int] = {"safe": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class ModeCapability:
    """What a given execution mode is allowed to do."""

    name: str
    allow_network: bool       # may send any request to the target at all
    allow_active: bool        # active enumeration/probing beyond passive OSINT
    allow_authenticated: bool # may use credentials / sessions / tokens
    allow_mutation: bool      # may send state-changing (still non-destructive) requests
    requires_approval: bool   # park sensitive actions for human approval
    max_risk: str             # highest tool/action risk auto-permitted
    description: str

    def permits_risk(self, risk: str) -> bool:
        return _RISK_RANK.get(risk, 99) <= _RISK_RANK.get(self.max_risk, 0)


_CAPS: dict[str, ModeCapability] = {
    "passive-only": ModeCapability(
        "passive-only", allow_network=False, allow_active=False,
        allow_authenticated=False, allow_mutation=False, requires_approval=False,
        max_risk="safe", description="Passive OSINT only; no requests to the target.",
    ),
    "safe-active": ModeCapability(
        "safe-active", allow_network=True, allow_active=True,
        allow_authenticated=False, allow_mutation=False, requires_approval=False,
        max_risk="safe", description="Non-destructive active enumeration and safe probing.",
    ),
    "authenticated-safe": ModeCapability(
        "authenticated-safe", allow_network=True, allow_active=True,
        allow_authenticated=True, allow_mutation=False, requires_approval=False,
        max_risk="medium", description="Credentialed reads (sessions/tokens); no mutation.",
    ),
    "approval-required": ModeCapability(
        "approval-required", allow_network=True, allow_active=True,
        allow_authenticated=True, allow_mutation=True, requires_approval=True,
        max_risk="high", description="State-changing/sensitive actions, each human-gated.",
    ),
    "report-only": ModeCapability(
        "report-only", allow_network=False, allow_active=False,
        allow_authenticated=False, allow_mutation=False, requires_approval=False,
        max_risk="safe", description="No network; rebuild reports from stored evidence.",
    ),
    "recovery": ModeCapability(
        "recovery", allow_network=False, allow_active=False,
        allow_authenticated=False, allow_mutation=False, requires_approval=False,
        max_risk="safe", description="Degraded mode after an error; passive + audit only.",
    ),
}

# Sensible default mode per recon/test profile.
_PROFILE_DEFAULT_MODE: dict[str, str] = {
    "budget": "passive-only",
    "recon-only": "safe-active",
    "default": "safe-active",
    "high-quality": "safe-active",
    "local": "safe-active",
}


def capability(mode: str) -> ModeCapability:
    """Return the capability matrix for ``mode`` (defaults to safe-active)."""
    return _CAPS.get(mode, _CAPS["safe-active"])


def resolve_mode(mode: str | None, *, profile: str | None = None) -> str:
    """Normalize a requested mode, falling back to the profile default."""
    if mode and mode in _CAPS:
        return mode
    return default_mode_for_profile(profile)


def default_mode_for_profile(profile: str | None) -> str:
    return _PROFILE_DEFAULT_MODE.get(profile or "", "safe-active")


def mode_allows_risk(mode: str, risk: str) -> bool:
    return capability(mode).permits_risk(risk)
