"""Tests for WTH-AI v1.0 platform."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.agents.planner import PlannerAgent
from wth_ai.config import WTHConfig
from wth_ai.executor.evidence import EvidencePipeline
from wth_ai.memory.engagement_memory import EngagementMemory
from wth_ai.memory.learning import HuntMemoryLearning
from wth_ai.models import AttackSurface, ExecutorResult, ExecutorTask, PermissionMode, PlanOutput
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.orchestrator import WTHOrchestrator
from wth_ai.permission_manager import PermissionManager
from wth_ai.recon.graph import AdaptiveReconGraph
from wth_ai.recon.tool_pipeline import ToolPipeline
from wth_ai.scope_manager import ScopeManager
from wth_ai.skills.fusion import SkillFusionEngine
from wth_ai.tools.pipeline import ToolChainOrchestrator
from wth_ai.tools.registry import ToolRegistry


def test_scope_in_scope_wildcard():
    mgr = ScopeManager()
    mgr.set_scope(target="example.com", in_scope=["*.example.com", "example.com"], authorized=True)
    ok, _ = mgr.is_in_scope("api.example.com")
    assert ok
    ok, _ = mgr.is_in_scope("evil.com")
    assert not ok


def test_permission_denylist():
    pm = PermissionManager(PermissionMode.FULL_LAB)
    check = pm.check_command("rm -rf /")
    assert not check["allowed"]


def test_tool_registry_loads_200_plus():
    reg = ToolRegistry()
    assert len(reg.tools) >= 200


def test_tool_registry_summary():
    reg = ToolRegistry()
    s = reg.summary()
    assert s["total"] >= 200
    assert "installed" in s


def test_attack_surface_flat_observations():
    surface = AttackSurface(
        target="test.com",
        urls=["https://test.com/api/v1/users"],
        api_endpoints=["https://test.com/api/v1/users"],
    )
    obs = surface.flat_observations()
    assert any("api" in o for o in obs)


def test_engagement_memory_update_from_recon():
    mem = EngagementMemory("test-session-123")
    surface = AttackSurface(
        target="app.com",
        subdomains=["app.com", "api.app.com"],
        api_endpoints=["https://api.app.com/v1"],
        urls=["https://app.com/login"],
    )
    mem.update_from_recon(surface)
    assert "app.com" in mem.assets
    assert mem.tech_profile in {"api", "generic_web", "laravel"}


def test_evidence_pipeline_from_result():
    pipe = EvidencePipeline()
    result = ExecutorResult(
        task_id="T1", status="success", permission_check={},
        commands_used=["httpx -u https://x.com"],
        output_summary="[critical] CVE-2024-1234 found",
        evidence=["[critical] CVE-2024-1234"],
        observations=[], risk="high",
        recommended_next_step="verify",
    )
    ev = pipe.from_executor_result(result)
    assert len(ev) >= 1
    cands = pipe.to_findings_candidates(ev)
    assert cands[0]["severity"] in {"critical", "high", "medium"}


def test_recon_requires_scope():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    result = orch.handle_input("/recon")
    assert "target" in result.lower() or "scope" in result.lower()


def test_plan_empty_surface_warning():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    orch.state.scope = orch.scope_mgr.scope
    result = orch.handle_input("/plan Audit example.com")
    assert "recon" in result.lower() or "attack surface" in result.lower()


def test_doctor_report():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    report = orch.handle_input("/doctor")
    assert "WTH Doctor" in report
    assert "Tools" in report


def test_tools_list():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    report = orch.handle_input("/tools")
    assert "Tool Registry" in report


@patch("wth_ai.recon.tool_pipeline.shutil.which")
@patch.object(ToolPipeline, "_run_cmd")
def test_tool_pipeline_parses_httpx_output(mock_run_cmd, mock_which):
    mock_which.return_value = "/usr/bin/httpx"
    mock_run_cmd.return_value = (
        "https://test.example.com [200] [OK]\nhttps://api.test.example.com [403] [Forbidden]",
        "",
        0,
    )
    pipe = ToolPipeline()
    live = pipe._probe_http(["test.example.com"], pipe.output_root / "_test2")
    assert len(live) >= 1


def test_planner_profile_context():
    config = WTHConfig.load()
    planner = PlannerAgent(config, LLMProviderRouter(config), skill_router=None)
    from wth_ai.models import SessionState
    state = SessionState()
    state.metadata["last_recon"] = {"api_endpoints": 10}
    ctx = planner._build_context(state, "", "")
    assert "API PROFILE" in ctx or "ATTACK SURFACE" in ctx


def test_adaptive_recon_graph_api():
    graph = AdaptiveReconGraph()
    surface = AttackSurface(
        target="api.test.com",
        api_endpoints=["https://api.test.com/v1/users"] * 6,
        live_hosts=[],
    )
    plan = graph.plan_phases(surface)
    assert 4 in plan["phases"] or plan["profile"] == "api"


def test_tool_chain_orchestrator_init():
    chains = ToolChainOrchestrator()
    assert chains.registry is not None


def test_hunt_memory_learning():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        learning = HuntMemoryLearning(db)
        learning.record_from_finding("laravel", "idor", verified=True, endpoint="/api/users/{id}")
        rate = learning.success_rate("laravel", "idor")
        assert rate == 1.0
        ctx = learning.build_planner_context("laravel")
        assert "idor" in ctx.lower()


def test_plan_output_model():
    po = PlanOutput(confidence=0.7, planner_summary="test")
    assert po.confidence == 0.7


def test_executor_task_evidence_schema():
    task = ExecutorTask.from_dict({
        "id": "T1", "goal": "test", "target": "x.com",
        "permission_required": "read_only",
        "evidence_schema": {"type": "idor", "proof": "diff_user_data"},
    })
    assert task.evidence_schema["type"] == "idor"


def test_intel_feed_disabled():
    from wth_ai.intel.feed import IntelFeed
    config = WTHConfig.load()
    config.intel.enabled = False
    feed = IntelFeed(config)
    result = feed.gather()
    assert result.get("enabled") is False


def test_package_import_without_reconnaissance():
    """pip entry must not require legacy reconnaissance/ package."""
    import importlib
    import wth_ai
    importlib.reload(wth_ai)
    assert wth_ai.__version__ == "1.0.0"
    from wth_ai.orchestrator import WTHOrchestrator
    assert WTHOrchestrator is not None


def test_recon_package_exports():
    from wth_ai.recon import ReconOrchestrator, AdaptiveReconGraph, ToolPipeline
    assert ReconOrchestrator is not None
    assert AdaptiveReconGraph is not None
    assert ToolPipeline is not None


def test_entry_main_importable():
    from wth_ai.cli.entry import main
    assert callable(main)


def test_executor_runner_v2_resolve():
    from wth_ai.executor.runner_v2 import ExecutorRunnerV2
    runner = ExecutorRunnerV2()
    assert runner.registry is not None


def test_status_bar_render():
    from wth_ai.cli.status_bar import render_status_bar
    from wth_ai.models import SessionState
    config = WTHConfig.load()
    state = SessionState()
    state.scope.target = "test.com"
    bar = render_status_bar(state, config, tools_installed=10, tools_total=200, llm_online=True)
    assert "wth v" in bar
    assert "test.com" in bar


def test_asset_graph_from_recon():
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.models import AttackSurface, ParamCandidate
    g = AssetGraph("test-graph-session")
    surface = AttackSurface(
        target="app.com",
        urls=["https://app.com/api/users?id=1"],
        api_endpoints=["https://app.com/api/users"],
        parameters=[ParamCandidate(url="https://app.com/api/users", param="id")],
    )
    g.add_from_recon(surface)
    assert g.coverage()["nodes"] >= 2
    high = g.query_high_value_params(5)
    assert any("id" in n.data.get("param", "") for n in high)
    assert "ASSET GRAPH" in g.export_for_planner()


def test_hypothesis_engine():
    from wth_ai.brain.hypothesis_engine import HypothesisEngine
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.models import AttackSurface, ParamCandidate
    g = AssetGraph("test-hyp-session")
    g.add_from_recon(AttackSurface(
        target="api.com",
        parameters=[ParamCandidate(url="https://api.com/orders", param="order_id")],
        tech_stack={"framework": "laravel"},
    ))
    hyps = HypothesisEngine().generate(g, {"framework": "laravel"})
    assert len(hyps) >= 1
    assert any(h.get("vuln_class") == "idor" for h in hyps)


def test_chain_planner():
    from wth_ai.brain.chain_planner import ChainPlanner
    hyps = [{"title": "IDOR test", "vuln_class": "idor", "component": "https://x.com/api/1", "priority": 9}]
    chains = ChainPlanner().plan_from_hypotheses(hyps, "x.com")
    assert len(chains) >= 1
    tasks = ChainPlanner().chains_to_tasks(chains)
    assert tasks[0].evidence_schema.get("type") == "idor"


def test_session_engine_accounts():
    from wth_ai.http.session_engine import SessionEngine
    from wth_ai.models import Scope
    scope = Scope(test_accounts=["userA@test.com:pass1", "userB@test.com:pass2"])
    eng = SessionEngine(scope)
    assert eng.configured()
    assert len(eng.role_names()) == 2


def test_evidence_vault():
    import tempfile
    from pathlib import Path
    from wth_ai.evidence.vault import EvidenceVault
    with tempfile.TemporaryDirectory() as tmp:
        v = EvidenceVault("ev-test", base_dir=Path(tmp))
        eid = v.store(
            finding_id="FIND-1",
            request={"method": "GET", "url": "https://x.com/api/1"},
            response={"status": 200, "body": "secret data"},
            evidence_schema={"type": "idor", "proof": "diff_user_data"},
        )
        ok, missing = v.validate_schema("FIND-1", {"type": "idor", "proof": "diff_user_data"})
        assert eid.startswith("EV-")
        assert "Proof of Concept" in v.export_poc_block("FIND-1")


def test_param_ranker():
    from wth_ai.models import ParamCandidate
    from wth_ai.recon.deep.param_ranker import rank_params
    ranked = rank_params([
        ParamCandidate(url="https://x.com/search", param="q"),
        ParamCandidate(url="https://x.com/api/users", param="user_id"),
    ])
    assert ranked[0].score >= ranked[-1].score


def test_vuln_idor_executor_blocked_without_accounts():
    from wth_ai.executor.vuln.idor import IdorExecutor
    from wth_ai.models import ExecutorTask, PermissionMode, SessionState
    state = SessionState()
    task = ExecutorTask(id="T1", goal="idor", target="https://x.com/api/1", permission_required=PermissionMode.STANDARD_TESTING)
    result = IdorExecutor(state).execute(task)
    assert result.status == "blocked"


def test_graph_command():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    out = orch.handle_input("/graph")
    assert "Asset Graph" in out or "Nodes" in out


def test_terminal_bridge_http_probe():
    from wth_ai.terminal.bridge import AgentTerminalBridge
    from wth_ai.config import WTHConfig
    from wth_ai.permission_manager import PermissionManager
    from wth_ai.scope_manager import ScopeManager
    config = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    bridge = AgentTerminalBridge(config, PermissionManager(), scope, "test-tx-session")
    result = bridge.http_probe("HEAD", "https://example.com", actor="planner", purpose="test")
    assert result.transcript_id.startswith("TX-")
    assert bridge.transcripts.count() >= 1


def test_grounding_firewall():
    from wth_ai.agents.grounding import GroundingFirewall
    gf = GroundingFirewall()
    bad = gf.validate_claims("VERIFIED critical XSS found on /api")
    assert not bad.grounded or bad.violations
    good = gf.validate_claims("Possible reflection [TX:abc123] on param q")
    assert good.grounded


def test_recon_wizard_start():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    out = orch.handle_input("/recon")
    assert "Recon Wizard" in out or "domain" in out.lower()
    assert orch.state.metadata.get("pending_prompt") == "recon_target"


def test_agent_constitution_loads():
    from wth_ai.agents import load_agent_constitution
    text = load_agent_constitution()
    assert "Evidence Law" in text or "TTST" in text


def test_startup_message_minimal():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    msg = orch.startup_message()
    assert "wth v1.0.0" in msg
    assert ":=+++" not in msg  # no ASCII art


def test_setup_wizard_writes_user_config():
    from wth_ai.cli.setup_wizard import run_setup_wizard
    import wth_ai.config as cfg_mod
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "user.yaml"
        old = cfg_mod.USER_CONFIG_PATH
        cfg_mod.USER_CONFIG_PATH = cfg_path
        try:
            run_setup_wizard(non_interactive={
                "provider_type": "openrouter",
                "model": "test/model",
                "openrouter_api_key": "sk-test",
                "require_llm": False,
            })
            assert cfg_path.exists()
            text = cfg_path.read_text()
            assert "setup_complete: true" in text
            assert "test/model" in text
        finally:
            cfg_mod.USER_CONFIG_PATH = old


def test_realtime_blocks_offline_plan():
    config = WTHConfig.load()
    config.require_llm = True
    config.realtime_only = True
    orch = WTHOrchestrator(config, fresh=True)
    orch.scope_mgr.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    orch.state.scope = orch.scope_mgr.scope
    orch.state.metadata["last_recon"] = {"subdomains": 1}
    orch.state.attack_surface = ["https://example.com/api"]
    with patch.object(orch.client, "available", return_value=False):
        result = orch.handle_input("/plan Audit example.com")
    assert "setup" in result.lower() or "llm" in result.lower()


def test_llm_router_openrouter_provider():
    config = WTHConfig.load()
    router = LLMProviderRouter(config)
    assert router._get_or_create_provider("openrouter").provider_type == "openrouter"


def test_bot_session_manager_isolation():
    from wth_ai.bots.base import BotSessionManager
    config = WTHConfig.load()
    mgr = BotSessionManager(config)
    o1 = mgr.get_or_create("discord", "user1")
    o2 = mgr.get_or_create("discord", "user2")
    assert o1 is not o2
    assert mgr.session_count() == 2


def test_config_merge_user_yaml_overrides():
    import wth_ai.config as cfg_mod
    with tempfile.TemporaryDirectory() as tmp:
        user_cfg = Path(tmp) / "user.yaml"
        user_cfg.write_text("setup_complete: true\nrealtime_only: false\n", encoding="utf-8")
        old = cfg_mod.USER_CONFIG_PATH
        cfg_mod.USER_CONFIG_PATH = user_cfg
        try:
            cfg = WTHConfig.load()
            assert cfg.setup_complete is True
            assert cfg.realtime_only is False
        finally:
            cfg_mod.USER_CONFIG_PATH = old
