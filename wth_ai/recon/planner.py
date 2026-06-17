cursor/fase5-recon-platform-93fe
"""Recon tool planner: profile-aware, safe-by-default tool selection.

The planner never selects all 200+ tools. It picks a profile-sized, safe subset
(builtins first, then safe passive tools, then API enrichment). High-risk and
disabled tools are excluded with explicit reasons. Unavailable tools are still
*planned* (the runner skips them gracefully at execution time).
"""

from __future__ import annotations

import ipa<<<<<<< ddress
import re
from urllib.parse import urlparse

from wth_ai.recon.model import PlannedTool, ReconPlan, SkippedTool
from wth_ai.recon.registry import ReconToolRegistry

# Max tools selected per profile.
PROFILE_CAP: dict[str, int] = {
    "budget": 10,
    "recon-only": 30,
    "default": 40,
    "high-quality": 70,
    "local": 30,
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
    def __init__(self, registry: ReconToolRegistry | None = None) -> None:
        self.registry = registry or ReconToolRegistry()
=======
# wth_ai/recon/planner.py
"""
Recon Planner (Fase 5.1)

Simple but effective planner that selects tools based on:
- Profile (budget, default, recon-only, high-quality)
- Risk level
- Permission requirements
- Enabled status

This is the foundation. Will be expanded in later sub-phases.
"""

from typing import List, Dict

from .registry import ToolRegistry
from .models import ToolSpec, ReconPlan


class ReconPlanner:
    """Selects appropriate tools for a recon job."""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
 main

    def plan(
        self,
        target: str,
 cursor/fase5-recon-platform-93fe
        *,
        profile: str = "recon-only",
        realtime: bool = False,
        allowlist: set[str] | None = None,
    ) -> ReconPlan:
        if profile not in PROFILE_CAP:
            profile = "recon-only"
        cap = PROFILE_CAP[profile]
        ttype = detect_target_type(target)

        plan = ReconPlan(target=target, target_type=ttype, profile=profile, realtime=realtime)

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

        # 2) other tools by category priority until cap reached
        for category in _CATEGORY_PRIORITY:
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
                avail = "available" if self.registry.is_available(spec.name) else "planned (install to run)"
                selected_names.append(spec.name)
                plan.selected.append(
                    PlannedTool(spec.name, spec.category, spec.execution_mode, avail)
                )

        return plan

    def _eligible(
        self, name: str, profile: str, allowlist: set[str] | None
    ) -> tuple[bool, str]:
        spec = self.registry.get(name)
        if spec is None:
            return False, "unknown tool"
        if allowlist is not None and name not in allowlist:
            return False, "not in policy allowlist"
        if not self.registry.is_enabled(name):
            return False, "disabled_by_default" if not spec.enabled_by_default else "disabled by operator"
        if spec.risk_level != "safe" and not self.registry._overrides.get(name, False):
            return False, f"risk={spec.risk_level} (enable explicitly)"
        if profile not in spec.allowed_profiles:
            return False, f"not allowed in profile '{profile}'"
        if spec.execution_mode in ("manual", "stub"):
            return False, f"{spec.execution_mode}: not auto-runnable"
        if spec.execution_mode == "api" and not self.registry.is_available(name):
            return False, "api key missing"
        return True, "ok"

        profile: str = "default",
        max_risk: str = "safe",
        max_tools: int = 40,
    ) -> ReconPlan:
        """
        Generate a recon plan.

        Args:
            target: The target domain/IP/URL
            profile: budget | default | recon-only | high-quality | local
            max_risk: safe | medium | high
            max_tools: hard limit on number of tools
        """
        all_tools = self.registry.list_tools(enabled_only=True)

        # Filter by risk
        filtered = self.registry.get_tools_by_risk(max_risk)

        # Profile-based selection (simple rules for Fase 5.1)
        if profile == "budget":
            filtered = [t for t in filtered if t.risk_level == "safe"][:15]
        elif profile == "recon-only":
            filtered = filtered[:35]
        elif profile == "high-quality":
            filtered = filtered[:60]
        else:  # default
            filtered = filtered[:30]

        # Apply hard limit
        selected = filtered[:max_tools]

        skipped = []
        for tool in all_tools:
            if tool not in selected:
                reason = "risk_too_high" if tool.risk_level == "high" else "profile_limit"
                skipped.append({"name": tool.name, "reason": reason})

        return ReconPlan(
            target=target,
            profile=profile,
            selected_tools=selected,
            skipped_tools=skipped,
            total_tools=len(all_tools),
        )
 main
