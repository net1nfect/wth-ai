"""Supervisor agent and CLI UI tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_NO_ANIMATION", "1")

from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.agents.tool_registry import tools_for_actor
from wth_ai.cli.ui.animations import animations_enabled
from wth_ai.cli.ui.renderer import render_response
from wth_ai.cli.ui.tool_trace import ToolTracePanel
from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator
from wth_ai.recon.agent_bridge import ReconAgentBridge
from rich.console import Console
from io import StringIO


def test_supervisor_tool_registry_count():
    tools = tools_for_actor("supervisor")
    names = {t["function"]["name"] for t in tools}
    assert "scope_set" in names
    assert "recon_run_tool" in names
    assert "plan_hunt" in names
    assert len(names) >= 15


def test_scope_set_dispatch():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    ctx = orch._supervisor_ctx
    out = dispatch_supervisor_tool(ctx, "scope_set", {"target": "example.com", "authorized": True}, approved=True)
    assert "example.com" in out
    assert orch.state.scope.target == "example.com"


def test_recon_list_tools_dispatch():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    ctx = orch._supervisor_ctx
    dispatch_supervisor_tool(ctx, "scope_set", {"target": "example.com", "authorized": True}, approved=True)
    out = dispatch_supervisor_tool(ctx, "recon_list_tools", {"available_only": False}, approved=True)
    assert "ERROR" not in out
    assert "builtin" in out.lower() or "category" in out.lower()


def test_recon_bridge_builtin_offline():
    bridge = ReconAgentBridge("test-session", profile="recon-only")
    bridge.set_target("example.com")
    result = bridge.run_tool("python-dns", "example.com")
    assert result["status"] in {"success", "skipped", "failed"}


def test_handle_input_help():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    out = orch.handle_input("/help")
    assert "Slash Commands" in out or "/recon" in out


def test_handle_input_scope_natural_language_offline():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    out = orch.handle_input("scope example.com authorized")
    assert "example.com" in out.lower() or "offline" in out.lower()


def test_tool_trace_panel_render():
    panel = ToolTracePanel(max_lines=5)
    panel.add_pending("scope_set", {"target": "x.com"})
    panel.complete("scope_set", "ok", 10)
    text = panel.plain_log()
    assert "scope_set" in text


def test_animations_disabled_in_test():
    assert animations_enabled() is False


def test_renderer_plain():
    buf = StringIO()
    console = Console(file=buf, width=80, highlight=False)
    render_response(console, "# Hello\n\nworld")
    assert "Hello" in buf.getvalue()


def test_doctor_includes_supervisor():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    report = orch.handle_input("/doctor")
    assert "WTH Doctor" in report
