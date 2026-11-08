"""Item 4: independent verification attestation (Strix).

Penemuan tidak pernah mengonfirmasi dirinya sendiri. `actor_role` yang
eksplisit (`discoverer` vs `independent_verifier`) menaikkan SYARAT menuju
VERIFIED, tanpa mengubah default legacy (`actor_role=None`).
"""

from __future__ import annotations

import pytest

from wth_ai.findings_db import FindingsDB
from wth_ai.models import FindingState
from wth_ai.verify_gate import apply_verification, decide_state


def _vault(tmp_path, *, store: bool, reproductions: int = 0, controls: int = 0):
    from wth_ai.evidence.vault import EvidenceVault

    vault = EvidenceVault("sess-indep", base_dir=tmp_path)
    if store:
        vault.store(
            finding_id="F-1",
            request={"method": "GET", "url": "https://t.example/api/users/2"},
            response={"status": 200, "body": '{"user": "b"}'},
            reproductions=reproductions,
            controls=controls,
        )
    return vault


def _finding(state: FindingState = FindingState.CANDIDATE):
    return FindingsDB().add_finding({
        "id": "F-1", "title": "IDOR", "severity": "high", "state": state.value,
    })


# --- can_promote_to_verified ------------------------------------------------- #

def test_discoverer_cannot_promote():
    from wth_ai.redteam.attestation import (
        VerifierRole, can_promote_to_verified,
    )

    assert can_promote_to_verified(VerifierRole.DISCOVERER.value) is False
    assert can_promote_to_verified(VerifierRole.INDEPENDENT.value) is True


def test_legacy_none_role_can_promote():
    from wth_ai.redteam.attestation import can_promote_to_verified

    assert can_promote_to_verified(None) is True


# --- decide_state role gate -------------------------------------------------- #

def test_discoverer_cannot_verify_even_with_full_evidence(tmp_path):
    """Discoverer self-confirm dilarang: bukti lengkap pun tetap CANDIDATE."""
    state, assessment = decide_state(
        _finding().state, "verified",
        has_request_response=True, reproductions=2, controls=1,
        actor_role="discoverer",
    )
    assert state is FindingState.CANDIDATE
    assert assessment.level == "confirmed"


def test_independent_verifier_promotes_with_full_evidence(tmp_path):
    state, _ = decide_state(
        _finding().state, "verified",
        has_request_response=True, reproductions=2, controls=1,
        actor_role="independent_verifier",
    )
    assert state is FindingState.VERIFIED


def test_legacy_none_role_still_promotes(tmp_path):
    """Default lama (actor_role=None) tidak berubah."""
    state, _ = decide_state(
        _finding().state, "verified",
        has_request_response=True, reproductions=2, controls=1,
    )
    assert state is FindingState.VERIFIED


def test_actor_role_does_not_raise_without_evidence():
    """Tanpa bukti, actor_role tidak memaksa ke atas."""
    state, _ = decide_state(
        _finding().state, "verified",
        has_request_response=False, reproductions=0, controls=0,
        actor_role="independent_verifier",
    )
    assert state is FindingState.CANDIDATE


# --- apply_verification records attestation --------------------------------- #

def test_apply_verification_blocks_discoverer(tmp_path):
    db = FindingsDB()
    finding = _finding()
    db.findings["F-1"] = finding
    state = apply_verification(
        db, finding, {"verdict": "verified"},
        evidence_vault=_vault(tmp_path, store=True, reproductions=2, controls=1),
        actor_role="discoverer",
    )
    assert state is FindingState.CANDIDATE
    assert db.findings["F-1"].state is FindingState.CANDIDATE
    # Tidak ada attestation yang direkam untuk self-confirm.
    assert not db.findings["F-1"].attestation


def test_apply_verification_records_attestation_for_independent(tmp_path):
    db = FindingsDB()
    finding = _finding()
    db.findings["F-1"] = finding
    state = apply_verification(
        db, finding, {"verdict": "verified"},
        evidence_vault=_vault(tmp_path, store=True, reproductions=2, controls=1),
        actor_role="independent_verifier",
    )
    assert state is FindingState.VERIFIED
    att = db.findings["F-1"].attestation
    assert att.get("verifier_role") == "independent_verifier"
    assert att.get("level") == "confirmed"


def test_attest_verification_rejects_discoverer():
    from wth_ai.redteam.attestation import attest_verification

    with pytest.raises(ValueError):
        attest_verification(
            finding_id="F-1", actor_role="discoverer",
            verdict="verified", level="confirmed",
        )
