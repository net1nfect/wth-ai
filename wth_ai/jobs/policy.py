"""Job admission policy engine (Fase 4 base, hardened in Fase 6).

Safe-by-default. Rejects out-of-policy jobs before they enter the queue and
produces a structured :class:`PolicyDecision` that also drives execution-mode
gating, environment tagging, sensitive-action classification, rate limits, and
approval requirements. The legacy :meth:`evaluate` API is preserved.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from wth_ai.jobs.model import JOB_TYPES
from wth_ai.policy.execution_mode import capability, resolve_mode

# Job types that operate against a target and therefore require one.
_TARGET_REQUIRED: frozenset[str] = frozenset({"recon", "executor"})

# Hostname keyword -> environment tag (best-effort; never used to *reject*).
_ENV_HINTS: tuple[tuple[str, str], ...] = (
    ("localhost", "lab"), ("127.0.0.1", "lab"), ("::1", "lab"), (".local", "lab"),
    ("staging", "staging"), ("stg.", "staging"), ("stage.", "staging"),
    ("uat", "staging"), ("preprod", "staging"), ("pre-prod", "staging"),
    ("dev", "dev"), ("develop", "dev"), ("test", "dev"), ("qa", "dev"), ("sandbox", "dev"),
)

# Substrings that mark a *sensitive* action/target surface (raise scrutiny).
_SENSITIVE_HINTS: tuple[str, ...] = (
    "admin", "internal", "payment", "billing", "delete", "remove", "drop",
    "/api/v", "graphql", "upload", "import", "export", "auth", "login", "token",
    "password", "reset", "account", "user", "role", "privilege",
)

_DEFAULT_RATE_RPS: float = 2.0


def _normalize_host(target: str) -> str:
    t = (target or "").strip()
    if "://" in t:
        return (urlparse(t).hostname or t).lower().rstrip(".")
    return t.split("/")[0].split(":")[0].lower().rstrip(".")


def _host_matches(host: str, pattern: str) -> bool:
    host = host.lower()
    pattern = pattern.lower().strip()
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host == suffix or host.endswith("." + suffix)
    # CIDR membership
    if "/" in pattern:
        try:
            net = ipaddress.ip_network(pattern, strict=False)
            return ipaddress.ip_address(host) in net
        except ValueError:
            return False
    return host == pattern


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    target_kind: str = "unknown"      # domain|url|ip|cidr|local|none
    environment: str = "unknown"      # prod|staging|dev|lab|unknown
    execution_mode: str = "safe-active"
    sensitivity: str = "safe"         # safe|medium|high
    requires_approval: bool = False
    rate_rps: float = _DEFAULT_RATE_RPS
    notes: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "environment": self.environment,
            "target_kind": self.target_kind,
            "sensitivity": self.sensitivity,
            "rate_rps": self.rate_rps,
            "policy_notes": list(self.notes),
        }


class PolicyEngine:
    def __init__(self, config: Any = None) -> None:
        self.config = config
        self.default_rate_rps = self._config_rate(config)

    @staticmethod
    def _config_rate(config: Any) -> float:
        try:
            tools = getattr(config, "tools", None)
            rps = getattr(tools, "default_rate_limit_rps", None) if tools else None
            return float(rps) if rps else _DEFAULT_RATE_RPS
        except (TypeError, ValueError):
            return _DEFAULT_RATE_RPS

    # ------------------------------------------------------------------ #
    # Target / scope helpers
    # ------------------------------------------------------------------ #
    def classify_target(self, target: str | None) -> str:
        if not target or not target.strip():
            return "none"
        t = target.strip()
        host = _normalize_host(t)
        if "/" in host:  # shouldn't happen after normalize, defensive
            host = host.split("/")[0]
        try:
            ipaddress.ip_network(t, strict=False)
            if "/" in t:
                return "cidr"
        except ValueError:
            pass
        try:
            ip = ipaddress.ip_address(host)
            return "local" if (ip.is_private or ip.is_loopback) else "ip"
        except ValueError:
            pass
        if any(h in t.lower() for h in ("localhost", "127.0.0.1", "::1")):
            return "local"
        if "://" in t:
            return "url"
        return "domain" if "." in host else "local"

    def tag_environment(self, target: str | None, override: str | None = None) -> str:
        if override:
            return override
        t = (target or "").lower()
        for hint, tag in _ENV_HINTS:
            if hint in t:
                return tag
        return "prod" if t else "unknown"

    def classify_sensitivity(self, target: str | None, *, job_type: str = "recon") -> str:
        t = (target or "").lower()
        hits = sum(1 for h in _SENSITIVE_HINTS if h in t)
        if job_type == "executor":
            # active testing is inherently more sensitive
            return "high" if hits else "medium"
        if hits >= 2:
            return "high"
        if hits == 1:
            return "medium"
        return "safe"

    def check_allow_deny(
        self,
        target: str | None,
        *,
        allowlist: tuple[str, ...] | list[str] | None = None,
        denylist: tuple[str, ...] | list[str] | None = None,
        forbidden: tuple[str, ...] = (),
    ) -> tuple[bool, str]:
        if not target:
            return True, ""
        host = _normalize_host(target)
        for pat in (denylist or ()):
            if pat and (_host_matches(host, pat) or pat.lower() in target.lower()):
                return False, f"target matches denylist '{pat}'"
        for pat in forbidden:
            if pat and pat.lower() in target.lower():
                return False, f"target matches forbidden pattern '{pat}'"
        if allowlist:
            if any(_host_matches(host, pat) or pat.lower() in target.lower() for pat in allowlist):
                return True, ""
            return False, f"target '{host}' not in allowlist"
        return True, ""

    # ------------------------------------------------------------------ #
    # Admission
    # ------------------------------------------------------------------ #
    def admit(
        self,
        *,
        type: str,
        target: str | None = None,
        profile: str | None = None,
        execution_mode: str | None = None,
        environment: str | None = None,
        forbidden: tuple[str, ...] = (),
        allowlist: tuple[str, ...] | list[str] | None = None,
        denylist: tuple[str, ...] | list[str] | None = None,
        safe_mode: bool = True,
    ) -> PolicyDecision:
        notes: list[str] = []
        if type not in JOB_TYPES:
            return PolicyDecision(False, f"unknown job type '{type}'")
        if not safe_mode:
            return PolicyDecision(False, "safe_mode disabled is not permitted")
        if type in _TARGET_REQUIRED and not (target and target.strip()):
            return PolicyDecision(False, f"job type '{type}' requires a target")

        ok, reason = self.check_allow_deny(
            target, allowlist=allowlist, denylist=denylist, forbidden=forbidden
        )
        if not ok:
            return PolicyDecision(False, reason)

        kind = self.classify_target(target)
        env = self.tag_environment(target, environment)
        sensitivity = self.classify_sensitivity(target, job_type=type)
        mode = resolve_mode(execution_mode, profile=profile)
        cap = capability(mode)

        # Production + sensitive surface => require approval regardless of mode.
        requires_approval = cap.requires_approval
        if env == "prod" and sensitivity == "high":
            requires_approval = True
            notes.append("prod + high-sensitivity surface -> approval required")
        if type == "executor" and not cap.allow_active:
            return PolicyDecision(
                False,
                f"execution mode '{mode}' forbids active testing required by executor jobs",
            )

        rate = self.default_rate_rps
        if env == "prod":
            rate = min(rate, 1.0)
            notes.append("prod target -> rate capped at 1.0 rps")

        return PolicyDecision(
            allowed=True,
            reason="",
            target_kind=kind,
            environment=env,
            execution_mode=mode,
            sensitivity=sensitivity,
            requires_approval=requires_approval,
            rate_rps=rate,
            notes=notes,
        )

    # ------------------------------------------------------------------ #
    # Legacy API (kept stable for callers/tests)
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        *,
        type: str,
        target: str | None = None,
        forbidden: tuple[str, ...] = (),
        safe_mode: bool = True,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). reason is empty when allowed."""
        decision = self.admit(type=type, target=target, forbidden=forbidden, safe_mode=safe_mode)
        return decision.allowed, decision.reason
