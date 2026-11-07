"""Batas tool-call per run (paritas TurnToolCallLimiter Strix)."""

from __future__ import annotations

from typing import Any, cast

from wth_ai.agents.tool_loop import AgentToolLoop
from wth_ai.llm.limits import ToolCallBudget, filter_response_calls


def test_total_budget_blocks_call_after_limit() -> None:
    budget = ToolCallBudget(max_total=2, max_per_tool=10, max_in_response=10)
    assert budget.check("read") is None
    budget.spend("read")
    assert budget.check("write") is None
    budget.spend("write")
    msg = budget.check("scan")
    assert msg is not None
    assert "2" in msg


def test_per_tool_budget_blocks_repeat() -> None:
    budget = ToolCallBudget(max_total=10, max_per_tool=1, max_in_response=10)
    assert budget.check("read") is None
    budget.spend("read")
    msg = budget.check("read")
    assert msg is not None
    assert "read" in msg


def test_zero_limit_disables_budget() -> None:
    budget = ToolCallBudget(max_total=0, max_per_tool=0, max_in_response=0)
    for _ in range(50):
        assert budget.check("read") is None
        budget.spend("read")


def test_filter_response_calls_keeps_prefix() -> None:
    calls = [{"name": "tool", "arguments": {"i": i}} for i in range(5)]
    kept, dropped = filter_response_calls(calls, limit=2)
    assert [c["arguments"]["i"] for c in kept] == [0, 1]
    assert dropped == 3


def test_filter_response_calls_limit_zero_keeps_all() -> None:
    calls = [{"name": "tool", "arguments": {}} for _ in range(3)]
    kept, dropped = filter_response_calls(calls, limit=0)
    assert len(kept) == 3
    assert dropped == 0


def test_observe_refuses_third_identical_call() -> None:
    budget = ToolCallBudget(max_total=50, max_per_tool=50, max_in_response=50)
    args = {"path": "a.txt"}
    assert budget.observe("read", args) is None
    assert budget.observe("read", args) is None
    msg = budget.observe("read", args)
    assert msg is not None
    assert "identik" in msg


def test_observe_treats_argument_order_as_identical() -> None:
    budget = ToolCallBudget()
    assert budget.observe("read", {"a": 1, "b": 2}) is None
    assert budget.observe("read", {"b": 2, "a": 1}) is None
    assert budget.observe("read", {"a": 1, "b": 2}) is not None


def test_observe_allows_different_arguments() -> None:
    budget = ToolCallBudget()
    for i in range(10):
        assert budget.observe("read", {"path": f"{i}.txt"}) is None


def test_unknown_tool_message_is_recoverable() -> None:
    loop = AgentToolLoop(cast(Any, None), cast(Any, None))
    msg = loop._dispatch_tool("tidak_ada", {}, actor="executor", approved=False)
    assert "tidak dikenal" in msg
    assert "tidak_ada" in msg
