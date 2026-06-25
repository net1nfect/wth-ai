# tests/test_recon_engine.py
"""Smoke tests for the canonical Fase 5/6 Recon Engine API.

These complement the exhaustive ``test_recon_platform.py`` suite and pin the
public ``ReconToolRegistry`` / ``ReconPlanner`` / ``handler_recon`` contract.
Fully offline: every network entrypoint is monkeypatched.

Follows AGENTS.md preference: CLI/library-first testing.
"""

from __future__ import annotations

import pytest

from wth_ai.recon import ReconEngine, ReconPlanner, ReconToolRegistry
from wth_ai.recon.planner import PROFILE_CAP


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.events import reset_event_bus

    reset_event_bus()
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    from wth_ai.recon import dns, tls
    from wth_ai.recon import http as rhttp

    def fake_fetch(url, *, method="GET", timeout=12, headers=None, allow_redirects=True):
        url = rhttp.normalize_url(url)
        return rhttp.HttpResponse(
            url=url,
            status=200,
            headers={"Content-Type": "text/html", "Server": "nginx"},
            text="<html><head><title>t</title></head><body>ok</body></html>",
        )

    monkeypatch.setattr(rhttp, "fetch", fake_fetch)
    monkeypatch.setattr(dns, "resolve_host", lambda h: [{"record_type": "A", "value": "93.184.216.34"}])
    monkeypatch.setattr(tls, "cert_info", lambda h, port=443, timeout=8: {
        "subject": ((("commonName", h),),),
        "issuer": ((("commonName", "CA"),),),
        "notAfter": "Jan 1 00:00:00 2030 GMT",
        "subjectAltName": (("DNS", h),),
    })
    return True


def test_registry_loads_catalog(tmp_home):
    registry = ReconToolRegistry()
    assert len(registry) >= 200, "Should have at least 200 tools"
    categories = {s.category for s in registry.tools.values()}
    assert len(categories) > 5


def test_registry_get_tool(tmp_home):
    registry = ReconToolRegistry()
    tool = registry.get("subfinder")
    assert tool is not None
    assert tool.category == "subdomain"


def test_planner_default_profile(tmp_home):
    plan = ReconPlanner().plan("example.com", profile="default")
    assert plan.target == "example.com"
    assert len(plan.selected) > 0
    assert len(plan.selected) <= PROFILE_CAP["default"]


def test_planner_budget_smaller_than_default(tmp_home):
    pl = ReconPlanner()
    budget = pl.plan("example.com", profile="budget")
    default = pl.plan("example.com", profile="default")
    assert len(budget.selected) <= PROFILE_CAP["budget"]
    assert len(budget.selected) < len(default.selected)


def test_handler_recon_integration(tmp_home, offline):
    """handler_recon runs the real engine and returns the canonical shape."""
    from wth_ai.jobs.handlers import handler_recon

    class FakeJob:
        job_id = "JOB-smoke"
        target = "example.com"
        profile = "recon-only"
        metadata = {"authorized": True}

    class FakeCtx:
        def __init__(self):
            self.logs = []

        def log(self, msg):
            self.logs.append(msg)

        def progress(self, current, total, message=""):
            pass

        def should_stop(self):
            return False

    result = handler_recon(FakeJob(), FakeCtx())
    assert result["type"] == "recon"
    assert result["status"] == "completed"
    assert result["target"] == "example.com"
