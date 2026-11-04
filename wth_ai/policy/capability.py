"""Decision vocabulary for the policy engine (ADR-009, ADR-010).

The engine answers exactly one question -- "may this command run?" -- and the
answer is built from three small pieces:

* :class:`RiskLevel` -- how bad the worst outcome of the tool is;
* :class:`ApprovalMode` -- whether that outcome needs a human before it happens;
* :class:`Capability` -- a named tool grant binding the two together;
* :class:`PermissionProfile` -- the set of grants a permission mode hands out.

Resolution is by *tool name only* (argv exact-match, per ADR-009): the profile
never inspects arguments. Argument-content risk is the engine's Action-Policy
layer, so a single list cannot drift out of sync with it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RiskLevel(str, Enum):
    """Severity of the worst realistic outcome of a granted tool."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalMode(str, Enum):
    """Whether a granted tool may run unattended."""

    AUTO_ALLOW = "auto_allow"
    REQUIRE_HUMAN = "require_human"
    ALWAYS_DENY = "always_deny"


@dataclass(frozen=True)
class Capability:
    """One tool grant."""

    name: str
    risk: RiskLevel = RiskLevel.LOW
    approval_mode: ApprovalMode = ApprovalMode.AUTO_ALLOW
    notes: str = ""

    @property
    def needs_approval(self) -> bool:
        return self.approval_mode is ApprovalMode.REQUIRE_HUMAN

    @property
    def denied(self) -> bool:
        return self.approval_mode is ApprovalMode.ALWAYS_DENY


@dataclass
class PermissionProfile:
    """A named set of tool grants.

    `wildcard` models the UNRESTRICTED tier: an unknown tool resolves to that
    capability instead of being refused. Everything else refuses unknown tools
    -- an allowlist that silently grows is not an allowlist.
    """

    name: str
    capabilities: dict[str, Capability] = field(default_factory=dict)
    wildcard: Capability | None = None

    def resolve(self, tool: str) -> Capability | None:
        """Capability for ``tool``, or ``None`` when the profile has no grant."""
        key = (tool or "").strip().lower()
        if not key:
            return None
        # A command may arrive as a path (``/bin/bash``); the grant is on the
        # basename, consistent with the engine's own tool extraction.
        key = key.rsplit("/", 1)[-1]
        cap = self.capabilities.get(key)
        if cap is not None:
            return cap
        if self.wildcard is not None:
            return Capability(
                name=key,
                risk=self.wildcard.risk,
                approval_mode=self.wildcard.approval_mode,
                notes=self.wildcard.notes,
            )
        return None

    def tools(self) -> tuple[str, ...]:
        return tuple(sorted(self.capabilities))

    def grant(self, capability: Capability) -> None:
        self.capabilities[capability.name] = capability

    def is_unrestricted(self) -> bool:
        return self.wildcard is not None

    def __contains__(self, tool: object) -> bool:
        return self.resolve(str(tool)) is not None
