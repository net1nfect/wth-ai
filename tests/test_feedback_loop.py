"""Tests for the closed-loop feedback, iterative cognitive hunt, and triage report."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.cognition import AdaptiveStrategySelector, FeedbackController
from wth_ai.analysis import TriageEngine
from wth_ai.config import WTHConfig
from wth_ai.models import AttackSurface, ExecutorResult, ParamCandidate
from wth_ai.orchestrator import WTHOrchestrator


class _FakeLearning:
    def __init__(self):
        self.records = []

    def record_from_finding(self, tech_stack, vuln_class, *, verified, tools=None, endpoint="", session_id=""):
        self.records.append((tech_stack, vuln_class, verified))

    def success_rate(self, tech_stack, vuln_class):
        rels = [r[2] for r in self.records if r[1] == vuln_class]
        return sum(rels) / len(rels) if rels else 0.0


# ---- FeedbackController ----

def test_feedback_records_execution_to_bandit_and_learning():
    sel = AdaptiveStrategySelector(exploration=0.0)
    learn = _FakeLearning()
    fb = FeedbackController(sel, learn)
    fb.record_execution(vuln_class="ssrf", strategy_name="oob_callback", success=True, tech_profile="api")
    fb.record_execution(vuln_class="ssrf", strategy_name="internal_probe", success=False, tech_profile="api")
    assert ("api", "ssrf", True) in learn.records
    # bandit now prefers the successful arm
    choice = sel.select("ssrf", tech_profile="api")
    assert choice is not None and choice.name == "oob_callback"


def test_feedback_verdict_kill_list():
    fb = FeedbackController(AdaptiveStrategySelector(), _FakeLearning())
    fb.record_verdict("Phantom XSS", "xss", verified=False)
    assert "Phantom XSS" in fb.killed_titles()
    fb.record_verdict("Real IDOR", "idor", verified=True)
    assert "Real IDOR" not in fb.killed_titles()


def test_feedback_snapshot():
    fb = FeedbackController(AdaptiveStrategySelector(), _FakeLearning())
    fb.record_execution(vuln_class="idor", strategy_name="dual_account_diff", success=True)
    snap = fb.snapshot()
    assert snap["executions_recorded"] == 1
    assert "strategy_arms" in snap


# ---- orchestrator feedback wiring ----

def test_orchestrator_records_execution_feedback():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    task = {"id": "PT-1", "target": "https://x.com/api/o", "evidence_schema": {"type": "idor"}}
    orch.state.metadata["task_strategy_map"] = {"PT-1": {"vuln_class": "idor", "strategy": "dual_account_diff"}}
    result = ExecutorResult(
        task_id="PT-1", status="success", permission_check={}, commands_used=["httpx"],
        output_summary="diff found", evidence=["[TX:1] cross-user"], observations=[],
        risk="high", recommended_next_step="verify",
    )
    orch._record_execution_feedback(task, result)
    assert orch.feedback.snapshot()["executions_recorded"] == 1


def test_orchestrator_killlist_suppresses_hypothesis_in_reason():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    orch.state.scope = orch.scope_mgr.scope
    orch.asset_graph.add_from_recon(AttackSurface(
        target="example.com",
        urls=["https://example.com/api/orders?user_id=1"],
        api_endpoints=["https://example.com/api/orders"],
        parameters=[ParamCandidate(url="https://example.com/api/orders", param="user_id")],
        tech_stack={"framework": "laravel"},
    ))
    before = orch.handle_input("/reason")
    # Find a title that appears, kill it, ensure it disappears.
    ranked = orch.state.metadata["cognition"]["ranked"]
    assert ranked
    title = ranked[0]["title"]
    orch.feedback.kill(title)
    after = orch.handle_input("/reason")
    after_titles = [r["title"] for r in orch.state.metadata["cognition"]["ranked"]]
    assert title not in after_titles


# ---- iterative cognitive hunt ----

def _seed(orch):
    orch.run_once("/permission standard")
    orch.scope_mgr.set_scope(
        target="example.com", in_scope=["example.com"], authorized=True,
        test_accounts=["a@x.com:p1", "b@x.com:p2"],
    )
    orch.state.scope = orch.scope_mgr.scope
    orch.asset_graph.add_from_recon(AttackSurface(
        target="example.com",
        urls=["https://example.com/api/orders?user_id=1", "https://example.com/fetch?url=1",
              "https://example.com/search?q=1"],
        api_endpoints=["https://example.com/api/orders"],
        parameters=[ParamCandidate(url="https://example.com/api/orders", param="user_id"),
                    ParamCandidate(url="https://example.com/fetch", param="url"),
                    ParamCandidate(url="https://example.com/search", param="q")],
        tech_stack={"framework": "laravel"},
    ))
    orch.state.metadata["tech_profile"] = "api"
    orch.state.metadata["attack_surface_obj"] = {"tech_stack": {"framework": "laravel"}}


def test_cognitive_hunt_offline_iterates():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    _seed(orch)
    out = orch.handle_input("/hunt --cognitive --iters=3")
    assert "Cognitive Hunt" in out
    assert "Iteration log" in out
    hunt = orch.state.metadata.get("hunt")
    assert hunt and hunt["iterations"] >= 1


def test_cognitive_hunt_empty_graph():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    out = orch.handle_input("/hunt --cognitive")
    assert "Cognitive Hunt" in out


# ---- triage report section (node 17) ----

def test_triage_report_section_has_weak_areas_and_retest():
    report = TriageEngine().triage([
        {"title": "IDOR", "component": "https://x.com/api/o", "vuln_class": "idor",
         "evidence": ["[TX:a]"], "severity": "high"},
        {"title": "SQLi", "component": "https://x.com/p?id=1", "vuln_class": "sqli",
         "evidence": ["[TX:b]"], "severity": "high"},
    ])
    section = report.format_report_section()
    assert "Prioritized Weak Areas" in section
    assert "Retest Checklist" in section
    assert "[ ]" in section


def test_report_includes_triage_section_offline():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    orch.state.scope = orch.scope_mgr.scope
    orch.state.metadata["pre_findings"] = [
        {"title": "idor_diff", "url": "https://example.com/api/o?id=1", "signal": "idor_diff",
         "anomaly_score": 0.7, "severity_hint": "high", "evidence": ["[TX:x]"]},
    ]
    out = orch.handle_input("/report")
    assert "Triage-Driven Findings" in out or "Prioritized Weak Areas" in out
