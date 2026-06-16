"""Tests for the WTH-AI Test Planning Layer (module taxonomy + planner)."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.cognition import CognitiveEngine
from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode, Scope
from wth_ai.orchestrator import WTHOrchestrator
from wth_ai.planning import module_for_vuln_class
from wth_ai.planning import modules as mod
from wth_ai.planning.test_planner import TestPlanner as Planner


IDOR = {"title": "IDOR on user_id @ /api/orders", "component": "https://x.com/api/orders?user_id=1",
        "vuln_class": "idor", "priority": 9, "evidence_needed": "cross-user data", "safe_test": "swap id"}
SSRF = {"title": "SSRF via url", "component": "https://x.com/fetch?url=1", "vuln_class": "ssrf", "priority": 8}
INFO = {"title": "Verbose error", "component": "https://x.com/", "vuln_class": "info_leak", "priority": 4,
        "evidence_needed": "stack trace"}


def _report(hyps, profile="api"):
    return CognitiveEngine().deliberate(hyps, tech_profile=profile)


# ---- module taxonomy ----

def test_module_lookup_covers_core_classes():
    def mid(vc: str) -> str:
        m = module_for_vuln_class(vc)
        assert m is not None
        return m.id

    assert mid("idor") == "access_control"
    assert mid("ssrf") == "injection"
    assert mid("xss") == "client_side"
    assert mid("misconfig") == "configuration"
    assert module_for_vuln_class("totally_unknown") is None


def test_modules_have_non_destructive_families():
    for m in mod.MODULES:
        assert m.non_destructive_families(), f"{m.id} has no non-destructive family"
        schema = m.evidence_schema(m.vuln_classes[0])
        assert schema["type"] == m.vuln_classes[0]
        assert schema["proof"]


# ---- planner gating ----

def test_planner_gates_idor_without_two_accounts():
    plan = Planner().build_plan(
        _report([IDOR]),
        scope=Scope(target="x.com"),  # no accounts
        permission_mode=PermissionMode.STANDARD_TESTING,
    )
    idor_tests = [t for t in plan.tests if t.vuln_class == "idor"]
    assert idor_tests and idor_tests[0].skipped
    assert "account" in idor_tests[0].skip_reason.lower()


def test_planner_allows_idor_with_two_accounts():
    scope = Scope(target="x.com", test_accounts=["a@x.com:p1", "b@x.com:p2"])
    plan = Planner().build_plan(
        _report([IDOR]), scope=scope, permission_mode=PermissionMode.STANDARD_TESTING,
    )
    execu = plan.executable()
    assert execu
    assert execu[0].module_id == "access_control"
    assert not execu[0].skipped
    assert execu[0].requires_auth


def test_planner_permission_gating():
    # SSRF/injection needs standard; read_only should defer it.
    plan = Planner().build_plan(
        _report([SSRF]), scope=Scope(target="x.com"),
        permission_mode=PermissionMode.READ_ONLY_RECON,
    )
    ssrf = [t for t in plan.tests if t.vuln_class == "ssrf"][0]
    assert ssrf.skipped
    assert "permission" in ssrf.skip_reason.lower()


def test_planner_budget_exhaustion():
    scope = Scope(target="x.com")
    plan = Planner().build_plan(
        _report([INFO, dict(INFO, title="Verbose error 2", component="https://x.com/a")]),
        scope=scope, permission_mode=PermissionMode.READ_ONLY_RECON,
        request_budget=1,  # too small for two cost>=1 tests
    )
    assert any(t.skipped and "budget" in t.skip_reason for t in plan.tests)


def test_planner_orders_by_cognition_rank():
    scope = Scope(target="x.com", test_accounts=["a:1", "b:2"])
    plan = Planner().build_plan(
        _report([INFO, IDOR, SSRF]), scope=scope, permission_mode=PermissionMode.STANDARD_TESTING,
    )
    execu = plan.executable()
    # IDOR (highest risk) should rank ahead of info_leak
    ranks = {t.vuln_class: t.rank for t in execu}
    assert ranks.get("idor", 99) < ranks.get("info_leak", 100)


def test_plan_emits_executor_tasks():
    scope = Scope(target="x.com", test_accounts=["a:1", "b:2"])
    plan = Planner().build_plan(
        _report([IDOR, SSRF]), scope=scope, permission_mode=PermissionMode.STANDARD_TESTING,
    )
    tasks = plan.to_executor_tasks()
    assert tasks
    assert all(t.permission_required for t in tasks)
    assert all(t.evidence_schema.get("type") for t in tasks)


def test_plan_serializable_and_formatted():
    scope = Scope(target="x.com", test_accounts=["a:1", "b:2"])
    plan = Planner().build_plan(_report([IDOR, SSRF, INFO]), scope=scope,
                                    permission_mode=PermissionMode.STANDARD_TESTING)
    d = plan.to_summary_dict()
    assert d["executable"] >= 1
    assert "Test Plan" in plan.format_cli()


# ---- orchestrator integration ----

def test_orchestrator_testplan_offline():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.run_once("/permission standard")
    orch.scope_mgr.set_scope(
        target="example.com", in_scope=["example.com"], authorized=True,
        test_accounts=["alice@x.com:p1", "bob@x.com:p2"],
    )
    orch.state.scope = orch.scope_mgr.scope
    from wth_ai.models import AttackSurface, ParamCandidate
    orch.asset_graph.add_from_recon(AttackSurface(
        target="example.com",
        urls=["https://example.com/api/orders?user_id=1"],
        api_endpoints=["https://example.com/api/orders"],
        parameters=[ParamCandidate(url="https://example.com/api/orders", param="user_id")],
        tech_stack={"framework": "laravel"},
    ))
    out = orch.handle_input("/testplan")
    assert "Test Plan" in out
    assert orch.state.metadata.get("test_plan") is not None


def test_testplan_command_registered():
    from wth_ai.slash_commands import SlashCommandHandler
    assert "/testplan" in SlashCommandHandler.COMMANDS
    cmd, args = SlashCommandHandler().parse("/testplan --budget=50")
    assert cmd == "/testplan"
    assert args == ["--budget=50"]
