"""Regression tests — WU-114: `Claim` schema + `ClaimStore` (ADR-007,
DATA-MODEL.md §5).

Scope decision (lebih sempit dari target penuh DATA-MODEL.md §5.1 -- lihat
docstring `wth_ai/claim_store.py`): WU ini membangun `Claim`/`ClaimStore`
sebagai fondasi nyata + teruji. Sequencing "dibuat saat planning,
claim_id masuk ActionIntent.claim_ids, broker append evidence_refs saat
eksekusi" BELUM diwiring -- broker (WU-102) belum menyentuh evidence sama
sekali. `VerifierAgent` belum memberi bobot beda untuk `captured_via`.
`EvidencePipeline`/prefix `EVD-` TIDAK disentuh (komponen lain yang sudah
bekerja, bukan bagian WU ini).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.claim_store import ClaimStore
from wth_ai.models import Claim, FindingState, SessionState


# ---------------------------------------------------------------------------
# Claim dataclass
# ---------------------------------------------------------------------------

def test_claim_defaults():
    claim = Claim(claim_id="CLAIM-1", text="probed /admin returns 200")
    assert claim.claim_type == "observation"
    assert claim.verification_status == FindingState.HYPOTHESIS
    assert claim.captured_via == "llm_assertion"
    assert claim.evidence_refs == []


def test_claim_to_dict_shape():
    claim = Claim(claim_id="CLAIM-1", text="x", verification_status=FindingState.VERIFIED)
    d = claim.to_dict()
    assert d["claim_id"] == "CLAIM-1"
    assert d["verification_status"] == "verified"  # enum -> .value string


# ---------------------------------------------------------------------------
# ClaimStore.create_claim_from_hypothesis()
# ---------------------------------------------------------------------------

def test_create_claim_generates_id():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "IDOR on /api/user/1"})
    assert claim.claim_id.startswith("CLAIM-")
    assert store.get_claim(claim.claim_id) is claim


def test_create_claim_preserves_explicit_id():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"claim_id": "CLAIM-fixed", "text": "x"})
    assert claim.claim_id == "CLAIM-fixed"


def test_create_claim_invalid_status_falls_back_to_hypothesis():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x", "verification_status": "not_a_real_status"})
    assert claim.verification_status == FindingState.HYPOTHESIS


# ---------------------------------------------------------------------------
# ClaimStore.append_evidence()
# ---------------------------------------------------------------------------

def test_append_evidence_valid_prefixes():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    store.append_evidence(claim.claim_id, "TX:abc123", "EV:def456")
    assert claim.evidence_refs == ["TX:abc123", "EV:def456"]


def test_append_evidence_skips_invalid_prefix():
    """DATA-MODEL.md §5: evidence_refs HANYA prefix TX:/EV: yang valid --
    sesuai vocabulary GroundingFirewall.TX_REF/EV_REF nyata."""
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    store.append_evidence(claim.claim_id, "EVD:old-prefix-style", "RANDOM:xyz")
    assert claim.evidence_refs == []


def test_append_evidence_dedupes():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    store.append_evidence(claim.claim_id, "TX:abc123")
    store.append_evidence(claim.claim_id, "TX:abc123")
    assert claim.evidence_refs == ["TX:abc123"]


def test_append_evidence_unknown_claim_id_returns_none():
    store = ClaimStore()
    assert store.append_evidence("CLAIM-doesnotexist", "TX:x") is None


# ---------------------------------------------------------------------------
# ClaimStore.update_verification_status() / list_claims()
# ---------------------------------------------------------------------------

def test_update_verification_status():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x"})
    store.update_verification_status(claim.claim_id, FindingState.VERIFIED)
    assert claim.verification_status == FindingState.VERIFIED


def test_list_claims_filters_by_finding_id_and_task_id():
    store = ClaimStore()
    store.create_claim_from_hypothesis({"text": "a", "finding_id": "FIND-1", "task_id": "T-1"})
    store.create_claim_from_hypothesis({"text": "b", "finding_id": "FIND-2", "task_id": "T-1"})

    assert len(store.list_claims(finding_id="FIND-1")) == 1
    assert len(store.list_claims(task_id="T-1")) == 2
    assert len(store.list_claims(finding_id="FIND-999")) == 0


# ---------------------------------------------------------------------------
# to_dict() / load_from_dict() round-trip
# ---------------------------------------------------------------------------

def test_round_trip_preserves_claim_id_and_evidence():
    store = ClaimStore()
    claim = store.create_claim_from_hypothesis({"text": "x", "finding_id": "FIND-1"})
    store.append_evidence(claim.claim_id, "TX:abc")

    restored = ClaimStore()
    restored.load_from_dict(store.to_dict())
    r = restored.get_claim(claim.claim_id)
    assert r is not None
    assert r.evidence_refs == ["TX:abc"]
    assert r.finding_id == "FIND-1"


# ---------------------------------------------------------------------------
# SessionState.claims -- aditif, backward-compat
# ---------------------------------------------------------------------------

def test_session_state_claims_default_empty():
    state = SessionState()
    assert state.claims == []


def test_session_state_to_dict_includes_claims():
    state = SessionState()
    state.claims = [{"claim_id": "CLAIM-1"}]
    assert state.to_dict()["claims"] == [{"claim_id": "CLAIM-1"}]


def test_session_state_from_dict_backward_compatible_without_claims_key():
    """Acceptance-adjacent: data sesi LAMA (sebelum WU-114, tanpa key
    'claims' sama sekali) tetap berhasil load tanpa error."""
    old_data = {"session_id": "old-session"}
    state = SessionState.from_dict(old_data)
    assert state.claims == []


def test_session_state_from_dict_preserves_claims():
    data = {"session_id": "x", "claims": [{"claim_id": "CLAIM-1", "text": "y"}]}
    state = SessionState.from_dict(data)
    assert state.claims == [{"claim_id": "CLAIM-1", "text": "y"}]
