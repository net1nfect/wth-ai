"""E2E hunt pipeline tests (mocked recon, no network)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.setenv("WTH_TEST_MODE", "1")
    monkeypatch.setenv("WTH_SKIP_SETUP", "1")


def test_asset_graph_breakdown():
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.graph.models import GraphNode, NodeType

    with tempfile.TemporaryDirectory() as tmp:
        graph = AssetGraph("breakdown-test", base_dir=Path(tmp))
        graph.add_node(GraphNode.create(NodeType.HOST, "api.example.com"))
        graph.add_node(GraphNode.create(NodeType.ENDPOINT, "https://api.example.com/v1"))
        graph.add_node(GraphNode.create(NodeType.PARAMETER, "https://api.example.com/v1?id=1", param="id"))
        bd = graph.breakdown()
        assert bd.get("host", 0) >= 1
        assert bd.get("endpoint", 0) >= 1
        assert bd.get("parameter", 0) >= 1


def test_event_bridge_forwards_job_progress():
    from wth_ai.cli.tui.bus import UiEventBus
    from wth_ai.cli.tui.event_bridge import unwire_platform_events, wire_platform_events
    from wth_ai.events import get_event_bus, reset_event_bus

    reset_event_bus()
    ui = UiEventBus()
    received: list[dict] = []
    ui.on("job_progress", lambda **kw: received.append(kw))
    handler = wire_platform_events(ui)
    try:
        get_event_bus().emit(
            "job.progress",
            job_id="job-abc",
            data={"current": 35, "total": 100, "message": "http probe"},
        )
        assert received
        assert received[0]["current"] == 35
        assert received[0]["message"] == "http probe"
    finally:
        unwire_platform_events(handler)
        reset_event_bus()


def test_report_job_seeds_hypotheses_from_graph(tmp_path, monkeypatch):
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.graph.models import GraphNode, NodeType
    from wth_ai.jobs.orchestrator_bridge import enrich_findings_for_report
    from wth_ai.findings_db import FindingsDB
    from wth_ai.models import SessionState

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    state = SessionState()
    state.session_id = "report-seed-test"
    graph = AssetGraph(state.session_id)
    graph.add_node(
        GraphNode.create(
            NodeType.PARAMETER,
            "https://api.example.com/user?id=1",
            param="id",
            url="https://api.example.com/user",
        )
    )
    graph.save()
    db = FindingsDB()
    added = enrich_findings_for_report(state, db)
    assert added >= 1
    assert db.hypotheses
    assert state.hypotheses


def test_hunt_pipeline_with_mocked_recon():
    from wth_ai.config import WTHConfig
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.graph.models import GraphNode, NodeType
    from wth_ai.loop.engagement_loop import EngagementLoop
    from wth_ai.models import ExecutorTask
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.update_from_dict({"target": "example.com", "in_scope": ["example.com"], "authorized": True})
    orch.state.scope = orch.scope_mgr.scope

    orch.asset_graph.add_node(GraphNode.create(NodeType.HOST, "api.example.com"))
    orch.asset_graph.add_node(
        GraphNode.create(
            NodeType.PARAMETER,
            "https://api.example.com/profile?user_id=1",
            param="user_id",
            url="https://api.example.com/profile",
        )
    )
    orch.asset_graph.save()
    orch.state.metadata["last_recon"] = {"job_id": "mock-job", "target": "example.com"}

    task = ExecutorTask(
        id="T-1",
        goal="Check IDOR",
        module="idor",
        target="https://api.example.com/profile",
        permission_required=orch.state.permission_mode,
        evidence_schema={"type": "idor"},
    )
    plan_result = {
        "planner_summary": "test plan",
        "hypotheses": [],
        "tasks": [task],
    }
    exec_result = MagicMock()
    exec_result.status = "completed"
    exec_result.to_dict.return_value = {"id": "T-1", "status": "completed", "output": "ok"}

    loop = EngagementLoop(orch)
    with patch.object(orch.planner, "plan", return_value=plan_result):
        with patch.object(orch.executor, "execute_module", return_value=exec_result):
            with patch.object(orch.evidence_vault, "validate_schema", return_value=(True, [])):
                with patch.object(orch.verifier, "verify_offline", return_value={"verdict": "needs_more_evidence"}):
                    with patch.object(orch, "_report", return_value="## Report\nmock report"):
                        out = loop.run("hunt idor on api", max_iterations=1)

    assert "Hunt v2" in out
    assert "Hypotheses:" in out
    assert orch.state.metadata.get("pre_hypotheses")
    assert "Iteration 1" in out
