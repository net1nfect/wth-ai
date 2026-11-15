"""Kontrak budget kontekstual — pengujian parametrik/properti tanpa Hypothesis.

Fokus: batas body tidak pernah dilanggar, report konsisten dengan output,
pinned/invarian tidak pernah dipotong, input budget tidak valid ditolak, dan
report tidak membocorkan isi konteks.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.context.builder import (
    BudgetedContextBuilder,
    ContextBudgetReport,
    ContextSection,
    normalize_budget,
)


def _body_of(out: str, invariant: str) -> str:
    if not invariant:
        return out
    if out == invariant:
        return ""
    assert out.startswith(invariant + "\n")
    return out[len(invariant) + 1:]


# ---------------------------------------------------------------------------
# properti body: budget kecil, tepat, besar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("budget", [1, 2, 3, 4, 5, 6, 7, 10, 20, 50, 100, 1000, 10**6])
def test_body_never_exceeds_budget_and_report_matches_actual_output(budget):
    invariant = "INV"
    sections = [
        ContextSection("a", "A" * 7, 10),
        ContextSection("b", "B" * 13, 5),
        ContextSection("c", "C" * 100, 1),
    ]
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        sections, budget=budget, invariant=invariant,
        overflow="keep_invariant", report=report,
    )
    body = _body_of(out, invariant)

    assert len(body) <= budget, f"body {len(body)} > budget {budget}"
    assert report.budget == budget
    assert report.invariant_chars == len(invariant)
    assert report.body_chars == len(body)
    assert report.total_chars == report.invariant_chars + report.body_chars
    assert len(out) == report.total_chars + (1 if invariant and body else 0)
    assert report.unbounded is False


@pytest.mark.parametrize("budget", [1, 2, 3, 4, 5, 8, 13, 21, 40, 100])
def test_truncation_marker_always_fits_inside_budget(budget):
    out = BudgetedContextBuilder().render_section(
        ContextSection("x", "X" * 1000), budget,
    )
    assert len(out) <= budget
    if len(out) < 1000:
        # Marker adaptif boleh hilang hanya saat budget lebih kecil dari marker
        # terpendek; yang tidak boleh adalah output melebihi budget.
        assert out == "X" * budget or "…" in out


def test_exact_boundary_has_no_truncation_or_marker():
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [
            ContextSection("a", "A" * 5, 2),
            ContextSection("b", "B" * 5, 1),
        ],
        budget=11, invariant="", report=report,
    )
    assert out == "AAAAA\nBBBBB"
    assert report.body_chars == 11
    assert report.truncated_sections == []
    assert report.dropped_sections == []


# ---------------------------------------------------------------------------
# section kosong / dropped / newline
# ---------------------------------------------------------------------------


def test_many_empty_sections_are_ignored_without_telemetry_noise():
    sections = [ContextSection(f"empty-{i}", "", 100 - i) for i in range(50)]
    sections.append(ContextSection("real", "DATA", 1))
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        sections, budget=100, invariant="INV", report=report,
    )
    assert out == "INV\nDATA"
    assert report.dropped_sections == []
    assert report.truncated_sections == []
    assert report.section_chars == {"real": 4}


def test_all_sections_after_budget_exhaustion_are_reported_dropped():
    sections = [
        ContextSection(f"s{i}", f"{i:02d}" * 50, 100 - i) for i in range(20)
    ]
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        sections, budget=5, invariant="", report=report,
    )
    assert len(out) <= 5
    assert report.truncated_sections == ["s0"]
    assert len(report.dropped_sections) == 19
    assert report.dropped_sections == [f"s{i}" for i in range(1, 20)]
    assert report.body_chars == len(out)


def test_newline_joiners_are_reserved_and_do_not_overflow_budget():
    # 3 section masing-masing 4 char + 2 newline = 14 char.
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [ContextSection(name, name * 4, 10 - i) for i, name in enumerate("abc")],
        budget=14, invariant="", report=report,
    )
    assert out == "aaaa\nbbbb\ncccc"
    assert report.body_chars == 14
    assert report.body_chars == len(out)
    assert report.truncated_sections == []
    assert report.dropped_sections == []


# ---------------------------------------------------------------------------
# invarian: mengonsumsi / melebihi budget
# ---------------------------------------------------------------------------


def test_invariant_exactly_filling_budget_leaves_no_body_and_is_reported():
    invariant = "I" * 20
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [ContextSection("body", "B" * 100, 10)],
        budget=20, invariant=invariant, overflow="keep_invariant", report=report,
    )
    assert out == invariant
    assert report.invariant_overflow is True
    assert report.invariant_chars == 20
    assert report.body_chars == 0
    assert report.dropped_sections == ["body"]


def test_invariant_overflow_keeps_all_invariant_and_reports_body_loss():
    invariant = "POLICY " * 100
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [ContextSection("body", "B" * 100, 10)],
        budget=30, invariant=invariant, overflow="keep_invariant", report=report,
    )
    assert out == invariant
    assert report.invariant_overflow is True
    assert report.total_chars > report.budget
    assert report.dropped_sections == ["body"]


def test_pinned_section_is_invariant_and_never_truncated():
    pinned = ContextSection("policy", "POLICY-" * 20, 0, pinned=True)
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [pinned, ContextSection("body", "B" * 100, 10)],
        budget=10, invariant="", overflow="keep_invariant", report=report,
    )
    assert pinned.text in out
    assert report.invariant_chars == len(pinned.text)
    assert report.invariant_overflow is True
    assert report.dropped_sections == ["body"]


def test_non_pinned_low_priority_section_is_truncated_or_dropped():
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        [
            ContextSection("policy", "POLICY-" * 20, 0, pinned=False),
            ContextSection("body", "B" * 100, 10),
        ],
        budget=100, invariant="", report=report,
    )
    assert "POLICY" not in out
    assert report.invariant_chars == 0
    assert "policy" in report.dropped_sections
    assert report.body_chars == 100


# ---------------------------------------------------------------------------
# input budget: nol/negatif/float/bool/nonnumerik
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("budget", [0, -1, -100, None])
def test_zero_and_negative_budget_remain_unbounded(budget):
    sections = [ContextSection("a", "A" * 1000, 10), ContextSection("b", "B" * 10, 1)]
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        sections, budget=budget, invariant="INV", report=report,
    )
    assert "A" * 1000 in out and "B" * 10 in out
    assert report.unbounded is True
    assert report.truncated_sections == []
    assert report.dropped_sections == []
    assert report.body_chars == len(_body_of(out, "INV"))


@pytest.mark.parametrize("raw,expected", [
    (0.0, 0), (0.9, 0), (-0.5, 0), (1.0, 1), (3.9, 3),
])
def test_float_budget_is_truncated_only_when_positive(raw, expected):
    assert normalize_budget(raw) == expected


@pytest.mark.parametrize("bad", [True, False, "abc", "12x", object(), [5], {"n": 5}])
def test_bool_and_non_numeric_budgets_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_budget(bad)
    with pytest.raises(ValueError):
        BudgetedContextBuilder().build_from_sections([], budget=bad)


# ---------------------------------------------------------------------------
# konsistensi report & tidak ada kebocoran isi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("budget", [0, 1, 10, 100, 10**6])
def test_report_accounting_is_consistent_and_content_free(budget):
    secret = "S3CR3T-CONTENT-MUST-NOT-LEAK"
    sections = [
        ContextSection("secret", f"{secret}-{'X' * 500}", 10),
        ContextSection("empty", "", 5),
    ]
    report = ContextBudgetReport()
    out = BudgetedContextBuilder().build_from_sections(
        sections, budget=budget, invariant="INV", overflow="keep_invariant",
        report=report,
    )
    blob = json.dumps(report.to_dict(), sort_keys=True, default=str)
    assert secret not in blob
    assert "X" * 50 not in blob
    assert report.total_chars == report.invariant_chars + report.body_chars
    assert report.body_chars == len(_body_of(out, "INV"))
    assert sum(report.section_chars.values()) <= report.body_chars
    assert len(report.truncated_sections) + len(report.dropped_sections) <= len(sections)
    assert report.budget == (0 if budget <= 0 else budget)
