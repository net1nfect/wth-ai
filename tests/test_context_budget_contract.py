"""Regression tests — Context budget: validasi input & kontrak invarian.

Failure mode nyata yang diperbaiki (reproduksi terverifikasi):
* budget negatif diperlakukan "unbounded" tanpa peringatan;
* budget non-int melempar TypeError mentah dari operator `<=`;
* `render_section` dapat melampaui budget karena marker truncation tidak
  dihitung (budget < marker);
* invarian yang lebih besar dari budget dilewati diam-diam, sehingga pemanggil
  tidak tahu batas kerasnya dilanggar;
* docstring `BudgetedContextBuilder.build` menyebut budget TOTAL padahal kode
  membatasi BODY (kontradiksi dokumen-vs-kode).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.context.builder import (
    BudgetExceededError,
    BudgetedContextBuilder,
    ContextBudgetReport,
    ContextSection,
    normalize_budget,
)
from wth_ai.models import SessionState


def _state() -> SessionState:
    s = SessionState()
    s.scope.target = "example.com"
    s.scope.authorized = True
    return s


# ---------------------------------------------------------------------------
# normalisasi budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    (0, 0), (-1, 0), (-999, 0), (None, 0),
    (5, 5), ("500", 500), ("0", 0), ("-10", 0),
    (500.0, 500), (3.9, 3),
])
def test_normalize_budget_is_explicit(raw, expected):
    assert normalize_budget(raw) == expected


def test_normalize_budget_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_budget("abc")
    with pytest.raises(ValueError):
        normalize_budget(object())


def test_negative_budget_does_not_silently_mean_infinite_without_opt_in():
    """Legacy `budget<=0` = unbounded is kept, but negative / invalid input no
    longer reaches it by accident: it is normalized explicitly to 0 (unbounded)
    and reported, not silently swallowed."""
    report = ContextBudgetReport()
    b = BudgetedContextBuilder()
    out = b.build_from_sections(
        [ContextSection("x", "DATA", 10)], budget=-5, invariant="INV", report=report,
    )
    assert "DATA" in out
    assert report.normalized_from == -5
    assert report.budget == 0  # unbounded
    assert report.unbounded is True


def test_non_int_budget_raises_value_error_not_typeerror():
    with pytest.raises(ValueError):
        BudgetedContextBuilder().build_from_sections(
            [ContextSection("x", "DATA", 10)], budget="abc", invariant="INV",
        )


# ---------------------------------------------------------------------------
# render_section tidak pernah melebihi budget
# ---------------------------------------------------------------------------


def test_render_section_never_exceeds_budget_even_below_marker():
    b = BudgetedContextBuilder()
    for budget in (1, 5, 10, 39, 40, 41, 100):
        out = b.render_section(ContextSection("x", "A" * 5000), budget)
        assert len(out) <= budget, f"budget={budget} len={len(out)}"


def test_render_section_zero_or_negative_budget_is_unbounded_but_validated():
    b = BudgetedContextBuilder()
    text = "A" * 100
    assert b.render_section(ContextSection("x", text), 0) == text
    assert b.render_section(ContextSection("x", text), -5) == text


# ---------------------------------------------------------------------------
# invarian > budget: harus eksplisit, tidak silent
# ---------------------------------------------------------------------------


def test_invariant_larger_than_budget_raises_by_default():
    inv = "INV" * 1000  # 3000 chars
    with pytest.raises(BudgetExceededError) as exc:
        BudgetedContextBuilder().build_from_sections(
            [ContextSection("x", "BODY", 10)], budget=50, invariant=inv,
        )
    assert exc.value.invariant_len == 3000
    assert exc.value.budget == 50


def test_invariant_larger_than_budget_with_overflow_policy_keeps_all_invariant():
    inv = "INV" * 1000
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [ContextSection("x", "BODY", 10)], budget=50, invariant=inv,
        overflow="keep_invariant", report=report,
    )
    assert inv in out            # nothing dropped from the safety block
    assert report.invariant_overflow is True
    assert report.total_chars > report.budget
    assert report.dropped_sections == ["x"]  # body lost entirely: no body budget left


def test_body_sections_dropped_when_invariant_consumes_budget():
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [ContextSection("x", "BODY", 10)], budget=50, invariant="I" * 50,
        overflow="keep_invariant", report=report,
    )
    assert "BODY" not in out
    assert report.dropped_sections == ["x"]


# ---------------------------------------------------------------------------
# prioritas & determinisme
# ---------------------------------------------------------------------------


def test_equal_priority_is_deterministic_by_name():
    b = BudgetedContextBuilder()
    a = b.build_from_sections(
        [ContextSection("b", "BB", 5), ContextSection("a", "AA", 5)], budget=0, invariant="",
    )
    z = b.build_from_sections(
        [ContextSection("a", "AA", 5), ContextSection("b", "BB", 5)], budget=0, invariant="",
    )
    assert a == z == "AA\nBB"


def test_lower_priority_dropped_before_higher():
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [
            ContextSection("critical", "C" * 100, 10),
            ContextSection("noise", "N" * 100, 0),
        ],
        budget=100, invariant="", report=report,
    )
    assert "C" * 100 in out
    assert "noise" in report.dropped_sections


def test_empty_optional_sections_ignored():
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [ContextSection("empty", "", 10), ContextSection("real", "DATA", 5)],
        budget=100, invariant="INV", report=report,
    )
    assert out == "INV\nDATA"
    assert "empty" not in report.truncated_sections


# ---------------------------------------------------------------------------
# observability
# ---------------------------------------------------------------------------


def test_report_tracks_budget_consumption():
    report = ContextBudgetReport()
    BudgetedContextBuilder().build_from_sections(
        [ContextSection("a", "A" * 50, 10), ContextSection("b", "B" * 500, 1)],
        budget=100, invariant="INV", report=report,
    )
    assert report.budget == 100
    assert report.invariant_chars == len("INV")
    assert report.body_chars <= 100
    assert report.total_chars == report.invariant_chars + report.body_chars
    assert "b" in report.truncated_sections


def test_report_section_char_accounting():
    report = ContextBudgetReport()
    BudgetedContextBuilder().build_from_sections(
        [ContextSection("a", "A" * 10, 10)], budget=100, invariant="", report=report,
    )
    assert report.section_chars == {"a": 10}


# ---------------------------------------------------------------------------
# invarian keamanan tetap utuh
# ---------------------------------------------------------------------------


def test_invariants_never_truncated_under_any_budget():
    b = BudgetedContextBuilder()
    state = _state()
    inv = b.invariant_block(state)
    for budget in (1, 10, 100, len(inv) - 1):
        report = ContextBudgetReport()
        out = b.build_from_sections(
            [ContextSection("x", "X" * 10_000, 10)], budget=budget,
            invariant=inv, overflow="keep_invariant", report=report,
        )
        assert inv in out, f"invariant hilang pada budget={budget}"
        assert "Target: example.com" in out
        assert "Authorized: True" in out
