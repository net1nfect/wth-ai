"""ActionMemory anti-repeat tests."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


def test_action_memory_blocks_info_tool_after_success(tmp_path):
    from wth_ai.memory.action_memory import ActionMemory

    am = ActionMemory("sess-1", base_dir=tmp_path)
    am.record("scope_show", {}, "SCOPE: ok")
    blocked, reason = am.should_block("scope_show", {})
    assert blocked is True
    assert "already succeeded" in reason


def test_action_memory_blocks_after_two_errors(tmp_path):
    from wth_ai.memory.action_memory import ActionMemory

    am = ActionMemory("sess-1", base_dir=tmp_path)
    am.record("recon_list_tools", {"available_only": True}, "ERROR: boom")
    blocked, _ = am.should_block("recon_list_tools", {"available_only": True})
    assert blocked is False
    am.record("recon_list_tools", {"available_only": True}, "ERROR: boom again")
    blocked2, reason2 = am.should_block("recon_list_tools", {"available_only": True})
    assert blocked2 is True
    assert "repeated" in reason2


def test_supervisor_dispatch_respects_action_memory(tmp_path):
    from wth_ai.orchestrator import WTHOrchestrator
    from wth_ai.config import WTHConfig
    from wth_ai.memory.action_memory import ActionMemory
    from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool

    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    ctx = orch._supervisor_ctx
    ctx.action_memory = ActionMemory(ctx.state.session_id, base_dir=tmp_path)

    out1 = dispatch_supervisor_tool(ctx, "scope_show", {}, approved=True)
    assert "SCOPE" in out1
    out2 = dispatch_supervisor_tool(ctx, "scope_show", {}, approved=True)
    assert out2.startswith("SKIP (memory):")

