"""Test completion gate (item 3): menolak finalisasi sampai semua terselesaikan."""

from __future__ import annotations

import pytest

from wth_ai.redteam.completion import (
    CompletionStatus,
    evaluate_completion,
)
from wth_ai.redteam.gate import exit_code


def test_exit_code_blocks_on_blocked_completion():
    r = evaluate_completion([{"state": "VERIFIED", "reported": False}])
    assert r.status is CompletionStatus.BLOCKED
    code = exit_code([], completion=r)
    assert code == 3


def test_exit_code_unaffected_without_completion():
    assert exit_code([]) == 0


def test_no_findings_is_completed():
    assert evaluate_completion([]).status is CompletionStatus.COMPLETED


def test_verified_finding_without_report_is_blocked():
    r = evaluate_completion([{"state": "VERIFIED", "reported": False}])
    assert r.status is CompletionStatus.BLOCKED
    assert r.unreported_verified_findings == 1


def test_all_dispositioned_is_completed():
    findings = [
        {"state": "FIXED", "reported": True},
        {"state": "ACCEPTED_RISK", "reported": True},
        {"state": "VERIFIED", "reported": True},
    ]
    assert evaluate_completion(findings).status is CompletionStatus.COMPLETED


def test_unresolved_finding_is_blocked():
    r = evaluate_completion([{"state": "HYPOTHESIS", "reported": False}])
    assert r.status is CompletionStatus.BLOCKED
    assert r.unresolved_findings == 1


def test_false_positive_without_report_is_completed():
    # Temuan yang dibuang (FALSE_POSITIVE) tanpa laporan bukan blocker.
    assert (
        evaluate_completion([{"state": "FALSE_POSITIVE", "reported": False}])
        .status
        is CompletionStatus.COMPLETED
    )


def test_blockers_are_reported_in_detail():
    r = evaluate_completion([{"state": "VERIFIED", "reported": False}])
    assert r.unreported_verified_findings == 1
    assert r.unresolved_findings == 1
