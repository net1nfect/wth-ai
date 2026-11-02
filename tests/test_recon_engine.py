# tests/test_recon_engine.py
"""Tests for Fase 5 Recon Engine."""

import os

import pytest

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.recon import ReconPlanner, ReconToolRegistry
from wth_ai.recon.model import ToolSpec


def test_registry_loads_catalog():
    registry = ReconToolRegistry()
    assert len(registry) >= 200, "Should have at least 200 tools"
    cats = {s.category for s in registry.tools.values()}
    assert len(cats) > 5


def test_registry_get_tool():
    registry = ReconToolRegistry()
    tool = registry.get("subfinder")
    assert tool is not None
    assert tool.category == "subdomain"


def test_planner_default_profile():
    registry = ReconToolRegistry()
    planner = ReconPlanner(registry)
    plan = planner.plan(target="example.com", profile="default")

    assert plan.target == "example.com"
    assert len(plan.selected) > 0
    assert len(plan.selected) <= 40


def test_planner_budget_profile():
    registry = ReconToolRegistry()
    planner = ReconPlanner(registry)
    plan = planner.plan(target="example.com", profile="budget")

    assert all(
        registry.get(p.tool) and registry.get(p.tool).risk_level == "safe"  # type: ignore[union-attr]
        for p in plan.selected
        if registry.get(p.tool) and registry.get(p.tool).risk_level != "safe"  # type: ignore[union-attr]
    ) or len(plan.selected) <= 10


def test_handler_recon_integration():
    """Test that handler_recon can use the new engine without crashing."""
    from wth_ai.jobs.handlers import handler_recon

    class FakeJob:
        target = "example.com"
        profile = "budget"
        job_id = "test-recon-job"
        metadata = {"authorized": True}

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
    assert "output_dir" in result or "error" in result or result.get("status") in ("completed", "blocked")
