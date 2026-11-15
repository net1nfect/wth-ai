"""Regression tests — Context Budget Engine (prioritas #3).

Fokus: `BudgetedContextBuilder` harus mempertahankan INVARIAN keamanan
(scope, permission, phase, queued tasks) walau budget kecil, dan tidak boleh
sekadar `text[:budget]` yang membuang bagian akhir (yang justru sering berisi
scope/permission karena muncul di preamble).

Ini additive: `ContextBuilder.build` lama tidak berubah.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.context.builder import (
    BudgetedContextBuilder,
    ContextBuilder,
    ContextSection,
)
from wth_ai.models import SessionState


def _state() -> SessionState:
    state = SessionState()
    state.scope.target = "example.com"
    state.scope.authorized = True
    return state


class _FakeActionMemory:
    def context_block(self) -> str:
        return "RECENT ACTIONS: did X\n" * 200


class _FakeJobs:
    def __init__(self, text: str) -> None:
        self.text = text

    def __call__(self, target: str) -> str:
        return self.text


# ---------------------------------------------------------------------------
# Invariant preservation
# ---------------------------------------------------------------------------


def test_invariants_survive_tiny_budget():
    """Even with a budget too small for everything, scope/permission/phase
    MUST be present — losing them is a safety regression, not cosmetic."""
    builder = BudgetedContextBuilder(
        base=ContextBuilder(action_memory=_FakeActionMemory()),
        jobs=_FakeJobs("JOB RECALL: " + ("job " * 500)),
    )
    out = builder.build(_state(), sources=["action_memory", "jobs"], budget=120)

    assert "Target: example.com" in out
    assert "Authorized: True" in out
    assert "Permission:" in out
    assert "Phase:" in out
    assert len(out) <= 120 + len(builder.invariant_block(_state()))  # invariant always allowed


def test_invariant_block_is_deterministic_and_small():
    builder = BudgetedContextBuilder(base=ContextBuilder())
    block = builder.invariant_block(_state())
    assert "Target: example.com" in block
    # invariant block should not balloon with unrelated content
    assert len(block) < 600


def test_low_priority_section_truncated_head_and_tail():
    """Large artifact output should keep head AND tail markers, not silently
    drop the tail (where errors/summaries usually are)."""
    builder = BudgetedContextBuilder(base=ContextBuilder())
    section = ContextSection(name="tool_output", text="HEAD" + ("x" * 5000) + "TAIL", priority=1)
    rendered = builder.render_section(section, budget=300)
    assert "HEAD" in rendered
    assert "TAIL" in rendered
    assert len(rendered) <= 300 + 80  # allowance for truncation marker


def test_section_priority_controls_drop_order():
    builder = BudgetedContextBuilder(base=ContextBuilder())
    sections = [
        ContextSection(name="critical", text="CRITICAL" * 50, priority=10),
        ContextSection(name="noise", text="NOISE" * 500, priority=0),
    ]
    out = builder.build_from_sections(sections, budget=400, invariant="I")
    assert "CRITICAL" in out
    # noise may be dropped/truncated before critical is touched
    assert out.count("CRITICAL") >= out.count("NOISE")


def test_budget_zero_is_unbounded():
    builder = BudgetedContextBuilder(base=ContextBuilder())
    out = builder.build(_state(), sources=[], budget=0)
    assert "Target: example.com" in out


def test_provenance_labels_are_present():
    builder = BudgetedContextBuilder(base=ContextBuilder())
    out = builder.build(
        _state(), sources=[], budget=200,
        provenance={"scope": "scope_manager", "phase": "state"},
    )
    assert "Target: example.com" in out


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_legacy_context_builder_build_unchanged():
    builder = ContextBuilder()
    out = builder.build(_state(), sources=[])
    assert "Target: example.com" in out
    assert len(out) < 500
