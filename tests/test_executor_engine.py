"""Fase 6 P5 tests: test planner + gated non-destructive execution engine."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")

import pytest

from wth_ai.executor.engine import ExecutionEngine
from wth_ai.planning import TestPlanner
from wth_ai.recon import http as rhttp


def _normalized() -> dict:
    return {
        "subdomains": [{"host": "example.com"}, {"host": "admin.example.com"}],
        "http_probes": [
            {"url": "https://example.com/", "status": 200},
            {"url": "https://admin.example.com/account", "status": 403},
        ],
        "headers": [{"url": "https://example.com/", "missing": ["Content-Security-Policy"]}],
        "endpoints": [
            {"url": "https://example.com/api/v1/users", "kind": "api"},
            {"url": "https://example.com/login", "kind": "auth"},
        ],
        "params": [{"url": "https://example.com/search?q=1", "name": "q", "interest": 5}],
    }


# --------------------------------------------------------------------------- #
# Test planner
# --------------------------------------------------------------------------- #
def test_planner_builds_tasks():
    man = TestPlanner().build("example.com", _normalized(), mode="safe-active")
    modules = {t.module for t in man.tasks}
    assert "http_probe" in modules
    assert "header_review" in modules
    assert "param_probe" in modules
    assert "api_review" in modules
    assert "auth_probe" in modules
    assert man.tasks  # non-empty


def test_planner_passive_mode_no_tasks():
    man = TestPlanner().build("example.com", _normalized(), mode="passive-only")
    assert man.tasks == []
    assert man.skipped


def test_planner_unauth_first_then_auth():
    man = TestPlanner().build("example.com", _normalized(), mode="authenticated-safe",
                              has_credentials=True)
    auth_idx = [i for i, t in enumerate(man.tasks) if t.authenticated]
    unauth_idx = [i for i, t in enumerate(man.tasks) if not t.authenticated]
    assert auth_idx and unauth_idx
    assert min(auth_idx) > max(unauth_idx)  # authenticated tasks come last


def test_planner_skips_auth_without_creds():
    man = TestPlanner().build("example.com", _normalized(), mode="authenticated-safe",
                              has_credentials=False)
    assert all(not t.authenticated for t in man.tasks)
    assert any("credentials" in s.get("reason", "") for s in man.skipped)


# --------------------------------------------------------------------------- #
# Execution engine (non-destructive, offline)
# --------------------------------------------------------------------------- #
@pytest.fixture
def fast_engine():
    return ExecutionEngine(scope_hosts={"example.com"}, rate_rps=10_000)


def test_engine_blocks_non_get(fast_engine, monkeypatch):
    from wth_ai.planning import TestTask
    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(url=url, status=200))
    task = TestTask(id="T1", module="http_probe", target_url="https://example.com/x", method="POST")

    class M:
        tasks = [task]
        request_budget = 100
    out = fast_engine.run(M())
    assert out["results"][0]["status"] == "skipped"
    assert "GET/HEAD" in out["results"][0]["detail"]


def test_engine_scope_guard(monkeypatch):
    from wth_ai.planning import TestTask
    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(url=url, status=200))
    eng = ExecutionEngine(scope_hosts={"example.com"}, rate_rps=10_000)
    task = TestTask(id="T1", module="http_probe", target_url="https://evil.com/x")

    class M:
        tasks = [task]
        request_budget = 100
    out = eng.run(M())
    assert out["results"][0]["status"] == "skipped"
    assert "scope" in out["results"][0]["detail"]


def test_engine_param_reflection_finding(fast_engine, monkeypatch):
    from wth_ai.planning import TestTask

    def fake(url, **k):
        # reflect the canary back verbatim
        body = "echo: " + url.split("=")[-1] if "=" in url else "<html></html>"
        return rhttp.HttpResponse(url=url, status=200, headers={}, text=body)
    monkeypatch.setattr(rhttp, "fetch", fake)
    task = TestTask(id="T1", module="param_probe", target_url="https://example.com/search",
                    params=["q"], sensitivity="medium")

    class M:
        tasks = [task]
        request_budget = 100
    out = fast_engine.run(M())
    assert out["findings"]
    f = out["findings"][0]
    assert f["category"] == "reflection"
    assert f["proven"] is False
    assert "body_sha256" in f["evidence"]


def test_engine_header_finding(fast_engine, monkeypatch):
    from wth_ai.planning import TestTask
    monkeypatch.setattr(rhttp, "fetch",
                        lambda url, **k: rhttp.HttpResponse(url=url, status=200, headers={"Server": "x"}, text="ok"))
    task = TestTask(id="T1", module="header_review", target_url="https://example.com/")

    class M:
        tasks = [task]
        request_budget = 100
    out = fast_engine.run(M())
    assert any(f["category"] == "headers" for f in out["findings"])


def test_engine_recovery_on_repeated_errors(monkeypatch):
    from wth_ai.planning import TestTask
    monkeypatch.setattr(rhttp, "fetch",
                        lambda url, **k: rhttp.HttpResponse(url=url, error="boom"))
    eng = ExecutionEngine(scope_hosts={"example.com"}, rate_rps=10_000, max_consecutive_errors=2)
    tasks = [TestTask(id=f"T{i}", module="http_probe",
                      target_url=f"https://example.com/{i}") for i in range(6)]

    class M:
        request_budget = 100
    m = M(); m.tasks = tasks
    out = eng.run(m)
    assert out["recovery"] is True
    assert out["stats"]["skipped"] >= 1  # later tasks skipped in recovery mode


def test_engine_high_finding_emits_event(monkeypatch):
    from wth_ai.events import EventBus
    from wth_ai.planning import TestTask
    monkeypatch.setattr(rhttp, "fetch",
                        lambda url, **k: rhttp.HttpResponse(url=url, status=200, headers={}, text="same"))
    eng = ExecutionEngine(scope_hosts={"example.com"}, rate_rps=10_000)
    task = TestTask(id="T1", module="access_control",
                    target_url="https://example.com/account/orders", authenticated=True)

    class M:
        request_budget = 100
    m = M(); m.tasks = [task]
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.type))
    out = eng.run(m, credentials={"cookie": "sid=abc"}, bus=bus, job_id="JOB-x")
    assert any(f["severity"] == "high" for f in out["findings"])
    assert "finding.high_priority" in seen
    assert "exec.task_completed" in seen
