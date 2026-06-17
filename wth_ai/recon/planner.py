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

    def plan(
        self,
        target: str,
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
