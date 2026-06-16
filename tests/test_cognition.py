"""Tests for the WTH-AI cognition layer (risk model, reasoning, strategy)."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.cognition import CognitiveEngine
from wth_ai.cognition.reasoning_engine import ReasoningEngine
from wth_ai.cognition.risk_model import RiskModel
from wth_ai.cognition.strategy_selector import AdaptiveStrategySelector
from wth_ai.cognition import signals
from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


IDOR = {
    "title": "BOLA/IDOR on user_id @ /api/orders",
    "component": "https://x.com/api/orders?user_id=1",
    "vuln_class": "idor",
    "priority": 9,
    "evidence_needed": "User B receives User A data",
    "safe_test": "swap id between accounts",
}
XSS = {
    "title": "Reflected XSS on q",
    "component": "https://x.com/search?q=1",
    "vuln_class": "xss",
    "priority": 6,
}
INFO = {
    "title": "Verbose error info leak",
    "component": "https://x.com/",
    "vuln_class": "info_leak",
    "priority": 2,
}


# ---- signals ----

def test_vuln_base_severity_ordering():
    # AGENT.md priority: auth bypass > privesc > IDOR > SSRF > XSS > info leak
    assert signals.vuln_base_severity("auth_bypass") > signals.vuln_base_severity("idor")
    assert signals.vuln_base_severity("idor") > signals.vuln_base_severity("ssrf")
    assert signals.vuln_base_severity("ssrf") > signals.vuln_base_severity("xss")
    assert signals.vuln_base_severity("xss") > signals.vuln_base_severity("info_leak")


def test_param_sensitivity_and_exposure():
    assert signals.param_sensitivity("user_id") == 1.0
    assert signals.param_sensitivity("q") < signals.param_sensitivity("user_id")
    assert signals.endpoint_exposure("https://x.com/admin/users") >= 0.85
    assert signals.endpoint_exposure("https://x.com/api/orders") >= 0.8
    assert signals.endpoint_exposure("https://x.com/") < 0.8


def test_normalize_vuln_class_handles_dashes():
    assert signals.normalize_vuln_class("Auth-Bypass") == "auth_bypass"
    assert signals.normalize_vuln_class(None) == "unknown"


# ---- risk model ----

def test_risk_model_ranks_idor_above_info_leak():
    model = RiskModel()
    high = model.score(IDOR, tech_profile="api")
    low = model.score(INFO, tech_profile="api")
    assert high.score > low.score
    assert high.severity_band in {"critical", "high"}
    assert low.severity_band in {"low", "info", "medium"}


def test_risk_score_is_explainable():
    score = RiskModel().score(IDOR, tech_profile="api")
    assert score.factors  # has contributing factors
    assert 0.0 <= score.confidence <= 1.0
    assert "idor" in score.rationale.lower()
    names = {n for n, _ in score.factors}
    assert "base_severity" in names


def test_learning_prior_lifts_score():
    class FakeLearning:
        def success_rate(self, tech_stack, vuln_class):
            return 1.0 if vuln_class == "idor" else 0.0

    model = RiskModel()
    without = model.score(IDOR, tech_profile="api")
    with_mem = model.score(IDOR, tech_profile="api", learning=FakeLearning())
    assert with_mem.score >= without.score
    assert any(n == "learning_prior" for n, _ in with_mem.factors)


# ---- reasoning engine ----

def test_reasoning_dedup_and_rank():
    engine = ReasoningEngine()
    report = engine.deliberate([IDOR, dict(IDOR), XSS, INFO], tech_profile="api")
    titles = [it.hypothesis["title"] for it in report.items]
    # duplicate IDOR merged
    assert titles.count(IDOR["title"]) == 1
    assert report.deduped >= 1
    # IDOR ranked first
    assert report.items[0].hypothesis["vuln_class"] == "idor"
    assert report.items[0].rank == 1
    assert 0.0 <= report.plan_confidence <= 1.0


def test_reasoning_kill_list():
    engine = ReasoningEngine()
    report = engine.deliberate([IDOR, XSS], tech_profile="api", killed_titles={XSS["title"]})
    titles = [it.hypothesis["title"] for it in report.items]
    assert XSS["title"] not in titles
    assert any(XSS["title"] in k for k in report.killed)


def test_reasoning_min_score_filters_noise():
    engine = ReasoningEngine(min_score=90.0)  # absurdly high -> everything killed
    report = engine.deliberate([XSS, INFO], tech_profile="generic_web")
    assert report.items == []


def test_reasoning_report_serializable_and_formatted():
    report = ReasoningEngine().deliberate([IDOR, XSS], tech_profile="api")
    d = report.to_summary_dict()
    assert d["ranked"][0]["risk_score"] >= d["ranked"][-1]["risk_score"]
    text = report.format_cli()
    assert "Reasoned Plan" in text
    assert "CRITICAL" in text or "HIGH" in text


# ---- adaptive strategy selector ----

def test_strategy_selector_picks_known_arm():
    sel = AdaptiveStrategySelector()
    choice = sel.select("idor", tech_profile="api")
    assert choice is not None
    assert choice.vuln_class == "idor"
    assert choice.tools


def test_strategy_selector_adapts_to_feedback():
    sel = AdaptiveStrategySelector(exploration=0.0)  # pure exploitation
    # Make one ssrf arm succeed repeatedly, the other fail.
    for _ in range(5):
        sel.record_outcome("ssrf", "oob_callback", success=True)
        sel.record_outcome("ssrf", "internal_probe", success=False)
    choice = sel.select("ssrf", tech_profile="api")
    assert choice is not None
    assert choice.name == "oob_callback"


def test_strategy_selector_exclude():
    sel = AdaptiveStrategySelector()
    choice = sel.select("ssrf", exclude={"oob_callback"})
    assert choice is not None
    assert choice.name != "oob_callback"


def test_strategy_permission_never_blank():
    sel = AdaptiveStrategySelector()
    for vc in ("idor", "xss", "ssrf", "auth", "unknown", "weird-class"):
        choice = sel.select(vc)
        assert choice is not None
        assert choice.permission in {"read_only", "standard", "elevated", "full_lab"}


# ---- facade + orchestrator integration ----

def test_cognitive_engine_attaches_strategy():
    eng = CognitiveEngine()
    report = eng.deliberate([IDOR, SSRF := {"title": "SSRF via url", "component": "https://x.com/f?url=1", "vuln_class": "ssrf", "priority": 8}], tech_profile="api")
    assert report.items[0].strategy is not None
    ranked = report.ranked_hypotheses()
    assert "recommended_strategy" in ranked[0]
    assert ranked[0]["risk_score"] >= ranked[-1]["risk_score"]


def test_orchestrator_reason_command_offline():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    orch.state.scope = orch.scope_mgr.scope
    # Seed graph from a recon-like surface so hypotheses exist.
    from wth_ai.models import AttackSurface, ParamCandidate
    surface = AttackSurface(
        target="example.com",
        urls=["https://example.com/api/orders?user_id=1"],
        api_endpoints=["https://example.com/api/orders"],
        parameters=[ParamCandidate(url="https://example.com/api/orders", param="user_id")],
        tech_stack={"framework": "laravel"},
    )
    orch.asset_graph.add_from_recon(surface)
    out = orch.handle_input("/reason")
    assert "Cognition" in out
    assert "Reasoned Plan" in out
    assert orch.state.metadata.get("cognition") is not None


def test_orchestrator_reason_targets_offline():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    from wth_ai.models import AttackSurface, ParamCandidate
    orch.asset_graph.add_from_recon(AttackSurface(
        target="example.com",
        urls=["https://example.com/api/u?id=1"],
        api_endpoints=["https://example.com/api/u"],
        parameters=[ParamCandidate(url="https://example.com/api/u", param="id")],
    ))
    out = orch.handle_input("/reason --targets")
    assert "Recon Focus" in out


def test_reason_command_registered():
    from wth_ai.slash_commands import SlashCommandHandler
    assert "/reason" in SlashCommandHandler.COMMANDS
    cmd, args = SlashCommandHandler().parse("/reason --targets")
    assert cmd == "/reason"
    assert args == ["--targets"]
