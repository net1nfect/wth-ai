"""Regression tests — budget context di PlannerAgent (lanjutan ADR-014).

Bug: `PlannerAgent._build_context()` menyusun 10+ potongan tanpa anggaran.
Blok besar (recon JSON, learnings, graph) dapat mendorong blok KEBIJAKAN
(SCOPE / PERMISSION MODE / PHASE / PENDING TASKS) ke luar jendela model atau
membuat prompt membengkak tanpa batas.

Kontrak: blok kebijakan selalu utuh; potongan lain dianggarkan dengan
prioritas dan diberi marker truncation eksplisit.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.agents.planner import PlannerAgent
from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode, SessionState


def _planner() -> PlannerAgent:
    return PlannerAgent(WTHConfig.load(), client=MagicMock())


def _state() -> SessionState:
    s = SessionState()
    s.scope.target = "example.com"
    s.scope.authorized = True
    s.scope.in_scope = ["example.com"]
    s.permission_mode = PermissionMode.STANDARD_TESTING
    return s


def _huge_learning() -> str:
    return "LEARNING-LINE " * 4000


def _policy_len_unused() -> int:
    return len(
        "\n".join([
            "PERMISSION MODE: standard",
            "PHASE: intake",
            f"SCOPE: {json.dumps(_state().scope.to_dict())}",
            "KNOWN ASSETS: []",
            "ATTACK SURFACE (0 items, sample): []",
            "COMPLETED TASKS: 0",
            "PENDING TASKS: 0",
        ])
    )


def test_policy_blocks_survive_large_context():
    planner = _planner()
    state = _state()
    state.task_queue = [{"id": "T1", "module": "dynamic"}]
    ctx = planner._build_context(
        state, resume_context="R", skill_context="S" * 20000,
        learning_context=_huge_learning(), graph_context="G" * 20000,
    )
    assert "PERMISSION MODE: standard" in ctx
    assert "PHASE:" in ctx
    assert '"target": "example.com"' in ctx
    assert "PENDING TASKS: 1" in ctx


def test_budget_truncation_is_marked(monkeypatch):
    budget = 4000
    planner = _planner()
    ctx = planner._build_context(
        _state(), resume_context="R", skill_context="S" * 20000,
        learning_context=_huge_learning(), graph_context="G" * 20000,
        context_budget=budget,
    )
    # Contract: `context_budget` bounds the BODY exactly; the pinned policy
    # block sits outside that budget. The report is the source of truth for
    # both numbers (no hand-copied approximation).
    from wth_ai.context.builder import ContextBudgetReport

    rep = ContextBudgetReport()
    planner._build_context(
        _state(), resume_context="R", skill_context="S" * 20000,
        learning_context=_huge_learning(), graph_context="G" * 20000,
        context_budget=budget, report=rep,
    )
    assert rep.body_chars <= budget
    assert rep.invariant_chars > 0
    assert "PERMISSION MODE: standard" in ctx
    assert "SCOPE:" in ctx
    assert "truncated" in ctx.lower()
    assert "PERMISSION MODE: standard" in ctx


def test_budget_zero_is_unbounded():
    planner = _planner()
    huge = "X" * 5000
    ctx = planner._build_context(_state(), resume_context=huge, context_budget=0)
    assert len(ctx) > 5000


def test_plan_signature_unchanged_and_context_budget_optional():
    planner = _planner()
    state = _state()
    # Existing call sites (no context_budget) must keep working.
    ctx = planner._build_context(state, "resume", "skill", "learning", "graph")
    assert "SCOPE:" in ctx
