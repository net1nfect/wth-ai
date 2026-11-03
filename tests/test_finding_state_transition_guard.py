"""Regression tests — WU-116: transition guard for `FindingState`
(`Finding.state` via `FindingsDB`, `Claim.verification_status` via
`ClaimStore`).

Bug ditutup (evidence, bukan spekulasi -- lihat docstring
`finding_state_transition_allowed()` di `wth_ai/models.py`): dua real call
site (`orchestrator._verify()` dengan `finding_id` eksplisit, dan tool
`verify_finding` di `agents/supervisor_dispatch.py`) mengambil finding by id
TANPA filter state saat ini -- sebelum WU ini, memanggil verify dua kali pada
finding_id yang sama bisa diam-diam membalik VERIFIED<->FALSE_POSITIVE tanpa
guard sama sekali.

Scope decision: transisi utk ACCEPTED_RISK/FIXED/RETEST_PASSED (nol real
caller hari ini) sengaja dibuat PERMISIF forward-only, didokumentasikan
eksplisit sbg asumsi (bukan evidenced usage) -- BUKAN dirancang sbg graph
lengkap yg diklaim teruji penuh (no-hallucination requirement).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.claim_store import ClaimStore
from wth_ai.findings_db import FindingsDB
from wth_ai.models import FindingState, InvalidFindingStateTransitionError, finding_state_transition_allowed


# ---------------------------------------------------------------------------
# finding_state_transition_allowed() -- pure function
# ---------------------------------------------------------------------------

def test_same_state_always_allowed():
    for s in FindingState:
        assert finding_state_transition_allowed(s, s) is True


@pytest.mark.parametrize("current", [FindingState.HYPOTHESIS, FindingState.NEEDS_EVIDENCE, FindingState.CANDIDATE])
@pytest.mark.parametrize("new", list(FindingState))
def test_pre_decision_states_allow_any_target(current, new):
    assert finding_state_transition_allowed(current, new) is True


def test_verified_allows_accepted_risk_and_fixed():
    assert finding_state_transition_allowed(FindingState.VERIFIED, FindingState.ACCEPTED_RISK) is True
    assert finding_state_transition_allowed(FindingState.VERIFIED, FindingState.FIXED) is True


def test_fixed_allows_retest_passed():
    assert finding_state_transition_allowed(FindingState.FIXED, FindingState.RETEST_PASSED) is True


def test_verified_rejects_direct_flip_to_false_positive():
    """Bug utama yang ditutup WU ini."""
    assert finding_state_transition_allowed(FindingState.VERIFIED, FindingState.FALSE_POSITIVE) is False


def test_false_positive_can_be_promoted_to_verified():
    """Ratchet 'surface, never hide' (koreksi WU-116/P1 exit review): finding
    yang keliru didismiss sebagai FALSE_POSITIVE BOLEH dipromosikan ke
    VERIFIED saat evidence baru datang -- arah AMAN, tidak bisa flip balik
    karena VERIFIED->FALSE_POSITIVE tetap diblok."""
    assert finding_state_transition_allowed(FindingState.FALSE_POSITIVE, FindingState.VERIFIED) is True


def test_false_positive_can_be_reopened_for_evidence():
    assert finding_state_transition_allowed(FindingState.FALSE_POSITIVE, FindingState.NEEDS_EVIDENCE) is True
    assert finding_state_transition_allowed(FindingState.FALSE_POSITIVE, FindingState.CANDIDATE) is True


def test_false_positive_is_not_terminal_but_forbids_triage_targets():
    """FALSE_POSITIVE bukan terminal, TAPI tetap tidak boleh langsung ke state
    triase-pasca-verified (ACCEPTED_RISK/FIXED/RETEST_PASSED) -- tidak masuk
    akal 'accept-risk' sebuah false positive, dan HYPOTHESIS (state paling
    mentah) juga bukan target re-open yang bermakna."""
    for new in (FindingState.ACCEPTED_RISK, FindingState.FIXED, FindingState.RETEST_PASSED, FindingState.HYPOTHESIS):
        assert finding_state_transition_allowed(FindingState.FALSE_POSITIVE, new) is False


def test_ratchet_cannot_flip_back_verified_after_promotion():
    """Bukti ratchet monotonik: FP->VERIFIED boleh, tapi VERIFIED->FP tetap
    diblok -- jadi tidak ada osilasi tak-terbatas, finding hanya bergerak
    MENUJU tersurface."""
    assert finding_state_transition_allowed(FindingState.FALSE_POSITIVE, FindingState.VERIFIED) is True
    assert finding_state_transition_allowed(FindingState.VERIFIED, FindingState.FALSE_POSITIVE) is False


def test_retest_passed_and_accepted_risk_are_terminal():
    for current in (FindingState.RETEST_PASSED, FindingState.ACCEPTED_RISK):
        for new in FindingState:
            if new == current:
                continue
            assert finding_state_transition_allowed(current, new) is False


def test_verified_rejects_regression_to_pre_decision():
    for new in (FindingState.HYPOTHESIS, FindingState.NEEDS_EVIDENCE, FindingState.CANDIDATE):
        assert finding_state_transition_allowed(FindingState.VERIFIED, new) is False


# ---------------------------------------------------------------------------
# FindingsDB.update_finding_state() -- integration, WTH_TEST_MODE=1 (raise)
# ---------------------------------------------------------------------------

def _db_with_finding(state: FindingState = FindingState.CANDIDATE) -> tuple[FindingsDB, str]:
    db = FindingsDB()
    f = db.add_finding({"title": "x", "state": state.value})
    return db, f.id


def test_candidate_to_verified_applies():
    db, fid = _db_with_finding(FindingState.CANDIDATE)
    result = db.update_finding_state(fid, FindingState.VERIFIED)
    assert result.state == FindingState.VERIFIED


def test_candidate_to_false_positive_applies():
    db, fid = _db_with_finding(FindingState.CANDIDATE)
    result = db.update_finding_state(fid, FindingState.FALSE_POSITIVE)
    assert result.state == FindingState.FALSE_POSITIVE


def test_reverify_already_verified_finding_raises_in_test_mode(monkeypatch):
    """Reachability-proof: replika langsung skenario bug (verify_finding
    dipanggil ulang pada finding_id yang sama, sekarang sudah VERIFIED)."""
    monkeypatch.setenv("WTH_TEST_MODE", "1")
    db, fid = _db_with_finding(FindingState.CANDIDATE)
    db.update_finding_state(fid, FindingState.VERIFIED)
    with pytest.raises(InvalidFindingStateTransitionError):
        db.update_finding_state(fid, FindingState.FALSE_POSITIVE)
    # state tidak berubah -- guard mem-block SEBELUM assignment
    assert db.findings[fid].state == FindingState.VERIFIED


def test_unknown_finding_id_still_returns_none():
    db = FindingsDB()
    assert db.update_finding_state("FIND-doesnotexist", FindingState.VERIFIED) is None


def test_kwargs_still_applied_on_legal_transition():
    db, fid = _db_with_finding(FindingState.CANDIDATE)
    result = db.update_finding_state(fid, FindingState.VERIFIED, severity="critical")
    assert result.severity == "critical"


# ---------------------------------------------------------------------------
# FindingsDB.update_finding_state() -- clamp behaviour outside test/strict mode
# ---------------------------------------------------------------------------

def test_illegal_transition_clamps_outside_test_mode(monkeypatch):
    """Production safety: TANPA WTH_TEST_MODE/WTH_STRICT_SM, transisi ilegal
    di-clamp (finding dikembalikan apa adanya, TIDAK crash, TIDAK
    diam-diam menerapkan state salah)."""
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WTH_STRICT_SM", raising=False)
    db, fid = _db_with_finding(FindingState.CANDIDATE)
    db.update_finding_state(fid, FindingState.VERIFIED)
    result = db.update_finding_state(fid, FindingState.FALSE_POSITIVE)
    assert result.state == FindingState.VERIFIED  # unchanged, not crashed
    assert db.findings[fid].state == FindingState.VERIFIED


def test_strict_sm_env_also_raises_without_test_mode(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("WTH_STRICT_SM", "1")
    db, fid = _db_with_finding(FindingState.CANDIDATE)
    db.update_finding_state(fid, FindingState.VERIFIED)
    with pytest.raises(InvalidFindingStateTransitionError):
        db.update_finding_state(fid, FindingState.FALSE_POSITIVE)


# ---------------------------------------------------------------------------
# ClaimStore.update_verification_status() -- same guard, shared implementation
# ---------------------------------------------------------------------------

def test_claim_hypothesis_to_verified_applies():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    result = store.update_verification_status(claim.claim_id, FindingState.VERIFIED)
    assert result.verification_status == FindingState.VERIFIED


def test_claim_reverify_raises_in_test_mode(monkeypatch):
    monkeypatch.setenv("WTH_TEST_MODE", "1")
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    store.update_verification_status(claim.claim_id, FindingState.VERIFIED)
    with pytest.raises(InvalidFindingStateTransitionError):
        store.update_verification_status(claim.claim_id, FindingState.FALSE_POSITIVE)
    assert claim.verification_status == FindingState.VERIFIED


def test_claim_illegal_transition_clamps_outside_test_mode(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WTH_STRICT_SM", raising=False)
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    store.update_verification_status(claim.claim_id, FindingState.VERIFIED)
    result = store.update_verification_status(claim.claim_id, FindingState.FALSE_POSITIVE)
    assert result.verification_status == FindingState.VERIFIED


def test_claim_unknown_id_still_returns_none():
    store = ClaimStore()
    assert store.update_verification_status("CLAIM-doesnotexist", FindingState.VERIFIED) is None
