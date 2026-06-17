# tests/test_recon_engine.py
"""
Tests for Fase 5 Recon Engine

Follows AGENTS.md preference: CLI/library-first testing.
"""

import pytest

from wth_ai.recon import ToolRegistry, ReconPlanner, ToolSpec


def test_registry_loads_manifest():
    registry = ToolRegistry()
    assert registry.count() >= 200, "Should have at least 200 tools"
    assert len(registry.get_categories()) > 5


def test_registry_get_tool():
    registry = ToolRegistry()
    tool = registry.get_tool("subfinder")
    assert tool is not None
    assert tool.category.startswith("recon.")


def test_planner_default_profile():
    registry = ToolRegistry()
    planner = ReconPlanner(registry)
    plan = planner.plan(target="example.com", profile="default", max_tools=20)

    assert plan.target == "example.com"
    assert len(plan.selected_tools) > 0
    assert len(plan.selected_tools) <= 20
    assert plan.total_tools >= 200


def test_planner_budget_profile():
    registry = ToolRegistry()
    planner = ReconPlanner(registry)
    plan = planner.plan(target="example.com", profile="budget", max_risk="safe")

    assert all(t.risk_level == "safe" for t in plan.selected_tools)
    assert len(plan.selected_tools) <= 15


def test_handler_recon_integration():
    """Test that handler_recon can use the new engine without crashing."""
    from wth_ai.jobs.handlers import handler_recon

    class FakeJob:
        target = "example.com"
        profile = "default"
        metadata = {}

    class FakeCtx:
        logs = []
        def log(self, msg):
            self.logs.append(msg)
        def progress(self, current, total, message=""):
            pass
        def should_stop(self):
            return False

    ctx = FakeCtx()
    result = handler_recon(FakeJob(), ctx)

    assert result["type"] == "recon"
    assert "plan_summary" in result or "error" in result
