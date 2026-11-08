"""Recon tool planner: profile-aware, safe-by-default tool selection.

The planner never selects all 200+ tools. It picks a profile-sized, safe subset
(builtins first, then safe passive tools, then API enrichment). High-risk and
disabled tools are excluded with explicit reasons. Unavailable tools are still
*planned* (the runner skips them gracefully at execution time).
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wth_ai.recon.adaptive import AdaptiveMatch, AdaptiveReconRules, ReconSignals
from wth_ai.recon.model import PlannedTool, ReconPlan, SkippedTool
from wth_ai.recon.registry import ReconToolRegistry

# Max tools selected per profile.
#
# Raised after measuring real output: `recon-only` capped at 30 while the
# registry holds hundreds of entries, so coverage was decided by an arbitrary
# ceiling rather than by what is installed and allowed. `max` is the "I want
# everything eligible" profile for a dedicated tester box.
PROFILE_CAP: dict[str, int] = {
    "budget": 15,
    "recon-only": 45,
    "default": 60,
    "high-quality": 100,
    "max": 160,
    "local": 45,
}

# Category execution priority (after builtins) for good coverage within a cap.
_CATEGORY_PRIORITY: tuple[str, ...] = (
    "subdomain", "dns", "http_probe", "tls", "archive", "crawl", "param",
    "js", "cloud", "api_schema", "port", "screenshot", "intel", "code_intel",
    "container", "mobile", "core",
)

_PRIVATE_HINTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def detect_target_type(target: str) -> str:
    t = target.strip()
    host = urlparse(t).hostname if "://" in t else t.split("/")[0].split(":")[0]
    host = (host or "").lower()
    if any(h in t.lower() for h in _PRIVATE_HINTS):
        return "local"
    try:
        ip = ipaddress.ip_address(host)
        return "local" if ip.is_private or ip.is_loopback else "ip"
    except ValueError:
        pass
    if "://" in t:
        return "url"
    if "." not in host:
        return "local"
    return "domain"


class ReconPlanner:
    def __init__(
        self,
        registry: ReconToolRegistry | None = None,
        *,
        rules_path: str | Path | None = None,
    ) -> None:
        self.registry = registry or ReconToolRegistry()
        self.adaptive = AdaptiveReconRules(rules_path)

    def plan(
        self,
        target: str,
        *,
        profile: str = "recon-only",
        realtime: bool = False,
        allowlist: set[str] | None = None,
        signals: ReconSignals | dict[str, Any] | None = None,
    ) -> ReconPlan:
        if profile not in PROFILE_CAP:
            profile = "recon-only"
        cap = PROFILE_CAP[profile]
        ttype = detect_target_type(target)

        if isinstance(signals, dict):
            sig = ReconSignals.from_normalized(signals)
        elif signals is not None:
            sig = signals
        else:
            sig = ReconSignals.from_target(target)
        adaptive_match = self.adaptive.evaluate(sig)

        plan = ReconPlan(
            target=target,
            target_type=ttype,
            profile=profile,
            realtime=realtime,
            adaptive_profile=adaptive_match.profile if adaptive_match.matched_rules else None,
            matched_rules=adaptive_match.matched_rules,
            boost_categories=list(adaptive_match.boost_categories),
            skip_categories=list(adaptive_match.skip_categories),
        )

        category_order = self._category_order(adaptive_match)

        # local targets only allowed under the local profile (policy guard)
        if ttype == "local" and profile != "local":
            plan.skipped.append(
                SkippedTool(tool="*", reason="local/private target requires --profile local")
            )

        selected_names: list[str] = []

        # 1) builtins first (always, cheap, core coverage)
        for spec in self.registry.tools.values():
            if spec.execution_mode != "builtin":
                continue
            ok, reason = self._eligible(spec.name, profile, allowlist)
            if ok:
                selected_names.append(spec.name)
                plan.selected.append(
                    PlannedTool(spec.name, spec.category, spec.execution_mode, "builtin core")
                )

        # 2) other tools by adaptive category priority until cap reached
        for category in category_order:
            if category in adaptive_match.skip_categories:
                continue
            for spec in sorted(self.registry.by_category(category), key=lambda s: s.name):
                if spec.execution_mode == "builtin" or spec.name in selected_names:
                    continue
                ok, reason = self._eligible(spec.name, profile, allowlist)
                if not ok:
                    plan.skipped.append(SkippedTool(spec.name, reason))
                    continue
                if len(plan.selected) >= cap:
                    plan.skipped.append(SkippedTool(spec.name, "profile cap reached"))
                    continue
                reason_bits = ["available" if self.registry.is_available(spec.name) else "planned (install to run)"]
                if category in adaptive_match.boost_categories:
                    reason_bits.append("adaptive boost")
                selected_names.append(spec.name)
                plan.selected.append(
                    PlannedTool(
                        spec.name, spec.category, spec.execution_mode, "; ".join(reason_bits),
                    )
                )

        return plan

    def refine_match(self, signals: ReconSignals | dict[str, Any]) -> AdaptiveMatch:
        if isinstance(signals, dict):
            signals = ReconSignals.from_normalized(signals)
        return self.adaptive.evaluate(signals)

    def _category_order(self, match: AdaptiveMatch) -> tuple[str, ...]:
        if not match.matched_rules:
            return _CATEGORY_PRIORITY
        boosted = [c for c in match.boost_categories if c in _CATEGORY_PRIORITY]
        rest = [c for c in _CATEGORY_PRIORITY if c not in boosted and c not in match.skip_categories]
        return tuple(boosted + rest)

    def _eligible(
        self, name: str, profile: str, allowlist: set[str] | None
    ) -> tuple[bool, str]:
        from wth_ai.engagement.mode import is_unrestricted

        spec = self.registry.get(name)
        if spec is None:
            return False, "unknown tool"
        if is_unrestricted() and os.environ.get("WTH_UNRESTRICTED") == "1":
            if spec.execution_mode in ("manual", "stub"):
                return False, f"{spec.execution_mode}: not auto-runnable"
            return True, "ok"
        if allowlist is not None and name not in allowlist:
            return False, "not in policy allowlist"
        if not self.registry.is_enabled(name):
            return False, "disabled_by_default" if not spec.enabled_by_default else "disabled by operator"
        if spec.risk_level != "safe" and not self.registry._overrides.get(name, False):
            if not is_unrestricted():
                return False, f"risk={spec.risk_level} (enable explicitly)"
        if profile not in spec.allowed_profiles:
            return False, f"not allowed in profile '{profile}'"
        if spec.execution_mode in ("manual", "stub"):
            return False, f"{spec.execution_mode}: not auto-runnable"
        if spec.execution_mode == "api" and not self.registry.is_available(name):
            return False, "api key missing"
        return True, "ok"
