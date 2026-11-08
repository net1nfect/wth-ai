"""P0-5: `FindingState.VERIFIED` hanya lewat veracity, bukan verdict LLM.

Sebelum ini tiga jalur (`orchestrator._verify`, `verify_finding`,
`engagement_loop`) menulis state langsung dari
`v["verdict"] == "verified"` — hasil regex atas teks model. Guard
`finding_state_transition_allowed` punya nol pemanggil.
"""

from __future__ import annotations

import pytest

from wth_ai.evidence.vault import EvidenceVault
from wth_ai.findings_db import FindingsDB
from wth_ai.models import FindingState, InvalidFindingStateTransitionError


def _db_with_finding(state: FindingState = FindingState.CANDIDATE) -> tuple[FindingsDB, object]:
    db = FindingsDB()
    finding = db.add_finding({
        "id": "F-1",
        "title": "IDOR on /api/users",
        "severity": "high",
        "state": state.value,
        "evidence": ["server mengembalikan data user lain"],
    })
    return db, finding


def _vault(tmp_path, *, store: bool, reproductions: int = 0, controls: int = 0,
           refuted: bool = False) -> EvidenceVault:
    vault = EvidenceVault("sess-p05", base_dir=tmp_path)
    if store:
        vault.store(
            finding_id="F-1",
            request={"method": "GET", "url": "https://t.example/api/users/2"},
            response={"status": 200, "body": '{"user": "b"}'},
            reproductions=reproductions,
            controls=controls,
            refuted_by_control=refuted,
        )
    return vault


def test_llm_verdict_alone_cannot_verify(tmp_path):
    """Verdict `verified` tanpa bukti tersimpan tetap memaksa CANDIDATE."""
    from wth_ai.verify_gate import apply_verification

    db, finding = _db_with_finding()
    state = apply_verification(
        db, finding, {"verdict": "verified"},
        evidence_vault=_vault(tmp_path, store=False),
    )
    assert state is FindingState.CANDIDATE
    assert db.findings["F-1"].state is FindingState.CANDIDATE


def test_verdict_with_full_evidence_verifies(tmp_path):
    """request/response + reproduksi + kontrol → VERIFIED."""
    from wth_ai.verify_gate import apply_verification

    db, finding = _db_with_finding()
    state = apply_verification(
        db, finding, {"verdict": "verified"},
        evidence_vault=_vault(tmp_path, store=True, reproductions=2, controls=1),
    )
    assert state is FindingState.VERIFIED
    assert db.findings["F-1"].state is FindingState.VERIFIED


def test_request_response_without_reproduction_or_control_is_capped(tmp_path):
    from wth_ai.verify_gate import apply_verification

    db, finding = _db_with_finding()
    state = apply_verification(
        db, finding, {"verdict": "verified"},
        evidence_vault=_vault(tmp_path, store=True, reproductions=1, controls=0),
    )
    assert state is FindingState.CANDIDATE


def test_refuting_control_lowers_the_llm_verdict(tmp_path):
    """LLM boleh dinaikkan... tidak: kontrol negatif yang sama bisa menurunkan."""
    from wth_ai.verify_gate import apply_verification

    db, finding = _db_with_finding()
    state = apply_verification(
        db, finding, {"verdict": "verified"},
        evidence_vault=_vault(tmp_path, store=True, reproductions=1, controls=1,
                              refuted=True),
    )
    assert state is FindingState.FALSE_POSITIVE


def test_findings_db_rejects_verified_to_hypothesis(monkeypatch):
    db, finding = _db_with_finding()
    db.update_finding_state("F-1", FindingState.VERIFIED)
    assert db.findings["F-1"].state is FindingState.VERIFIED

    monkeypatch.setenv("WTH_TEST_MODE", "1")
    with pytest.raises(InvalidFindingStateTransitionError):
        db.update_finding_state("F-1", FindingState.HYPOTHESIS)
    assert db.findings["F-1"].state is FindingState.VERIFIED

    # Tanpa mode ketat, transisi ilegal ditolak tanpa mematikan loop.
    monkeypatch.delenv("WTH_TEST_MODE")
    monkeypatch.delenv("WTH_STRICT_SM", raising=False)
    db.update_finding_state("F-1", FindingState.HYPOTHESIS)
    assert db.findings["F-1"].state is FindingState.VERIFIED


class _StubClient:
    """Menjawab `- Verdict: verified` seperti model yang yakin tanpa bukti."""

    def available(self) -> bool:
        return True

    def chat_for_role(self, *_args, **_kwargs) -> tuple[str, str]:
        return ("- Verdict: verified\n- Reason: terlihat rentan\n", "stub")


def test_verifier_agent_caps_its_own_verdict(tmp_path, monkeypatch):
    """`VerifierAgent.verify` sendiri tidak boleh melaporkan level yang
    melampaui bukti — call site apa pun menerima verdict yang sudah diturunkan."""
    from wth_ai.agents.verifier import VerifierAgent
    from wth_ai.config import WTHConfig
    from wth_ai.models import Finding, SessionState

    monkeypatch.chdir(__import__("pathlib").Path(__file__).resolve().parents[1])
    agent = VerifierAgent(WTHConfig(), _StubClient())
    finding = Finding(id="F-1", title="IDOR", severity="high",
                      state=FindingState.CANDIDATE, component="api",
                      affected_asset="https://t.example")
    verdict = agent.verify(finding, SessionState(), evidence_vault=_vault(tmp_path, store=False))

    assert verdict["verdict"] == "candidate"
    assert verdict["veracity"]["level"] == "unproven"