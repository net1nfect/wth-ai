"""Tests for roadmap platform improvements."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch):
    monkeypatch.setenv("WTH_TEST_MODE", "1")


def test_recon_graph_bridge_sync():
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.graph.recon_bridge import normalizer_from_dict, sync_recon_to_graph

    data = {
        "subdomains": [{"host": "api.example.com"}],
        "http_probes": [{"url": "https://api.example.com", "status": 200}],
        "endpoints": [{"url": "https://api.example.com/v1", "method": "GET"}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "normalized_result.json").write_text(json.dumps(data), encoding="utf-8")
        graph = AssetGraph("test-bridge-session", base_dir=base / "engagements")
        result = sync_recon_to_graph(base, graph, target="example.com")
        assert result["synced"] is True
        assert result["nodes"] >= 1

    norm = normalizer_from_dict(data)
    assert len(norm.subdomains) == 1


def test_shell_runner_no_shell():
    from wth_ai.shell_runner import run_command, tokenize_command

    assert tokenize_command('curl -s "https://example.com"') == ["curl", "-s", "https://example.com"]
    stdout, _stderr, code = run_command("echo hello")
    assert "hello" in stdout or code == 0


def test_tool_runner_uses_argv_not_shell():
    import inspect
    from wth_ai.tool_runner import ToolRunner

    src = inspect.getsource(ToolRunner._run_command)
    assert "shell=True" not in src


def test_handler_executor_not_mock():
    import inspect
    from wth_ai.jobs import handlers

    src = inspect.getsource(handlers.handler_executor)
    assert "mock" not in src.lower()


def test_handler_report_not_mock():
    import inspect
    from wth_ai.jobs import handlers

    src = inspect.getsource(handlers.handler_report)
    assert "mock report" not in src.lower()


def test_recon_diff_extended():
    from wth_ai.recon.diff import diff_results

    old = {
        "headers": [{"url": "https://a.com", "present": {"x-frame-options": "DENY"}, "missing": []}],
        "tls": [{"host": "a.com", "issuer": "Old CA", "not_after": "2025-01-01", "subject": "a.com"}],
        "findings": [],
    }
    new = {
        "headers": [{"url": "https://a.com", "present": {}, "missing": ["x-frame-options"]}],
        "tls": [{"host": "a.com", "issuer": "New CA", "not_after": "2026-01-01", "subject": "a.com"}],
        "findings": [{"title": "admin panel", "asset": "https://a.com/admin", "severity_hint": "high"}],
    }
    changes = diff_results(old, new)
    assert changes["header_posture_changed"]
    assert changes["tls_cert_changed"]
    assert changes["new_priority_targets"]


def test_api_adapters_graceful_skip():
    from wth_ai.recon.tools.api_adapters import run_api_adapter

    items, err = run_api_adapter("shodan", "example.com")
    assert items == []
    assert err == "api key missing"


def test_mcp_serve_protocol():
    from wth_ai.mcp.serve import McpServeRegistry

    reg = McpServeRegistry()
    init = reg.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["protocolVersion"]
    tools = reg.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert len(tools["result"]["tools"]) >= 3


def test_cron_enable_disable():
    from wth_ai.cron.store import CronStore

    with tempfile.TemporaryDirectory() as tmp:
        store = CronStore(path=Path(tmp) / "cron.json")
        entry = store.add("recon", "example.com", "every 6h")
        assert store.set_enabled(entry.cron_id, False)
        assert store.list()[0].enabled is False
        store.refresh_next_runs()
        assert store.list()[0].next_run_at is not None


def test_subagent_depth_limit():
    from wth_ai.agents.subagent.runner import SubagentRunner

    orch = MagicMock()
    orch.state.scope.target = "example.com"
    orch.state.metadata = {}
    runner = SubagentRunner(orch, depth=3)
    result = runner.run("recon", "scan")
    assert "depth limit" in result.lower()


def test_legacy_recon_orchestrator_removed():
    import importlib

    import pytest

    recon = importlib.import_module("wth_ai.recon")
    with pytest.raises(AttributeError):
        getattr(recon, "ReconOrchestrator")


def test_doctor_platform_check():
    from wth_ai.tools.doctor import ToolDoctor

    doc = ToolDoctor()
    plat = doc.check_platform()
    assert plat["graph_bridge"] is True
    assert plat["no_shell"] is True
    assert plat["job_handlers"] is True
