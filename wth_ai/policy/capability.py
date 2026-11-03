"""Capability-based permission primitives (ADR-009, WU-005).

Replaces the flat `MODE_TOOLS` allowlist as the enforcement unit.
`PermissionMode` (5 values, `wth_ai.models`) is KEPT as a backward-compatible
ceiling label (ADR-009 SS Why: rename would break serialization/slash-command/
tests with no security value) -- each mode maps to a `PermissionProfile`
bundling one or more `Capability`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class ApprovalMode(str, Enum):
    AUTO = "auto"
    NOTIFY = "notify"
    REQUIRE_HUMAN = "require_human"
    ALWAYS_DENY = "always_deny"


@dataclass(frozen=True)
class Capability:
    """A named bundle of tools sharing the same risk/approval semantics."""

    name: str
    tools: frozenset[str]
    risk: RiskLevel
    approval_mode: ApprovalMode
    allow_credential_injection: bool = False


@dataclass(frozen=True)
class PermissionProfile:
    """An ordered set of Capabilities. First matching Capability wins --
    ordering encodes precedence (e.g. interpreter-passthrough must be listed
    before a catch-all wildcard so it isn't shadowed)."""

    name: str
    capabilities: tuple[Capability, ...]

    def resolve(self, tool: str) -> Capability | None:
        """Return the Capability governing `tool`, or None if ungranted."""
        for cap in self.capabilities:
            if tool in cap.tools or "*" in cap.tools:
                return cap
        return None
