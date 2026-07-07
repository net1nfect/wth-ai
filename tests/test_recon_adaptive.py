"""Tests for adaptive recon rules wired into ReconPlanner."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_SKIP_SETUP", "1")

from wth_ai.recon.adaptive import AdaptiveReconRules, ReconSignals
from wth_ai.recon.planner import ReconPlanner


def test_graphql_url_rule_boosts_api_categories():
    rules = AdaptiveReconRules()
    match = rules.evaluate(ReconSignals(url_blob="https://app.example.com/graphql"))
    assert "graphql_api" in match.matched_rules
    assert "api_schema" in match.boost_categories or "crawl" in match.boost_categories


def test_api_only_rule_from_normalized_signals():
    rules = AdaptiveReconRules()
    data = {
        "http_probes": [{"url": "https://api.example.com"}],
        "endpoints": [{"url": f"https://api.example.com/v1/x{i}"} for i in range(6)],
        "alive_urls": [],
    }
    match = rules.evaluate(ReconSignals.from_normalized(data))
    assert "api_only" in match.matched_rules
    assert match.profile == "api"
    assert "cloud" in match.skip_categories or "js" in match.skip_categories


def test_planner_applies_adaptive_boost_to_plan():
    planner = ReconPlanner()
    data = {
        "http_probes": [{"url": "https://api.example.com"}],
        "endpoints": [{"url": f"https://api.example.com/v1/x{i}"} for i in range(6)],
    }
    plan = planner.plan("example.com", profile="recon-only", signals=data)
    assert plan.adaptive_profile == "api"
    assert "api_only" in plan.matched_rules
    boosted = [p for p in plan.selected if "adaptive boost" in p.reason]
    assert boosted


def test_planner_graphql_target_matches_url_rule():
    planner = ReconPlanner()
    plan = planner.plan("https://app.example.com/graphql", profile="recon-only")
    assert "graphql_api" in plan.matched_rules
