"""Batas tool-call per run (paritas TurnToolCallLimiter Strix)."""

from __future__ import annotations

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
