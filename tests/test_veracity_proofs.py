"""Integrasi proof profiles ke gate veracity (item 2 plan Strix)."""

from __future__ import annotations

import pytest

from wth_ai.redteam.proof_profiles import ProofVerdict
from wth_ai.veracity import LEVEL_CONFIRMED, ClaimAssessment, assess_claim


def test_assess_claim_accepts_category_and_proof_facts():
    # Bukti dasar penuh + proof profile INDEPENDENTLY_VERIFIED -> confirmed.
    a = assess_claim(
        has_request_response=True,
        reproductions=1,
        controls=1,
        category="ssrf",
        proof_facts={"negative_control": "x", "callback_received": True, "internal_url_evidence": "y"},
    )
    assert isinstance(a, ClaimAssessment)
    assert a.level == LEVEL_CONFIRMED


def test_assess_claim_rejected_by_profile_downgrades_to_candidate():
    # Bukti dasar penuh, tapi profil menolak (callback tidak ada) -> bukan confirmed.
    a = assess_claim(
        has_request_response=True,
        reproductions=1,
        controls=1,
        category="ssrf",
        proof_facts={"negative_control": "x", "callback_received": False},
    )
    assert a.level != LEVEL_CONFIRMED


def test_assess_claim_no_category_keeps_legacy_behavior():
    # Tanpa category, perilaku lama tetap.
    a = assess_claim(has_request_response=True, reproductions=1, controls=1)
    assert a.level == LEVEL_CONFIRMED
