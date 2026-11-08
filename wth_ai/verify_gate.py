"""P0-5: verdict LLM tidak pernah sendirian mengangkat finding ke VERIFIED.

Tiga jalur (`orchestrator._verify`, `verify_finding`, `engagement_loop`)
dulu menulis `finding.state` langsung dari `v["verdict"] == "verified"` —
hasil regex atas teks model. Satu-satunya jalan ke VERIFIED sekarang lewat
`veracity.assess_claim` atas bukti yang benar-benar tersimpan di evidence
vault, dan `FindingsDB` menolak transisi yang melanggar lifecycle.

Verdict LLM tetap dipakai, tetapi hanya sebagai batas atas yang boleh
menurunkan: model tidak bisa menaikkan status melewati bukti.
"""

from __future__ import annotations

from typing import Any

from wth_ai.models import FindingState
from wth_ai.redteam.attestation import attest_verification, can_promote_to_verified
from wth_ai.veracity import (LEVEL_CONFIRMED, LEVEL_REFUTED, ClaimAssessment,
                             assess_claim)


def _evidence_kwargs(evidence_vault: Any, finding_id: str) -> dict[str, Any]:
    if evidence_vault is None or not hasattr(evidence_vault, "claim_evidence"):
        return {"has_request_response": False, "reproductions": 0, "controls": 0,
                "refuted_by_control": False, "category": None, "proof_facts": None}
    return evidence_vault.claim_evidence(finding_id)


def decide_state(
    current: FindingState,
    verdict: str,
    *,
    has_request_response: bool = False,
    reproductions: int = 0,
    controls: int = 0,
    refuted_by_control: bool = False,
    category: str | None = None,
    proof_facts: dict | None = None,
    actor_role: str | None = None,
) -> tuple[FindingState, ClaimAssessment]:
    """State yang dibenarkan oleh bukti + verdict. Satu tempat, dua pemakai.

    `actor_role` (item 4): discoverer tidak pernah self-confirm — bukti
    lengkap pun dikunci di CANDIDATE. `None` (legacy) mempertahankan perilaku
    lama.
    """
    assessment = assess_claim(
        has_request_response=has_request_response,
        reproductions=reproductions,
        controls=controls,
        refuted_by_control=refuted_by_control,
        category=category,
        proof_facts=proof_facts,
    )
    said = (verdict or "").strip().lower()

    # Sudah VERIFIED: bukti tidak pernah dipakai untuk menurunkannya diam-diam.
    if current is FindingState.VERIFIED:
        return current, assessment

    if assessment.level == LEVEL_REFUTED or said == "false_positive":
        return FindingState.FALSE_POSITIVE, assessment
    if said != "verified":
        return current, assessment
    if assessment.level == LEVEL_CONFIRMED:
        if not can_promote_to_verified(actor_role):
            # Item 4: discoverer tidak pernah self-confirm.
            return FindingState.CANDIDATE, assessment
        return FindingState.VERIFIED, assessment
    return FindingState.CANDIDATE, assessment


def cap_verdict(finding_state: FindingState, verdict: dict[str, Any],
                evidence_vault: Any = None, finding_id: str = "",
                actor_role: str | None = None) -> dict[str, Any]:
    """Terapkan gate di dalam agent, supaya semua call site menerima verdict
    yang sudah diturunkan (bukan menunggu tiap pemanggil mengingatnya)."""
    fid = finding_id or str(verdict.get("finding_id", ""))
    state, assessment = decide_state(
        finding_state, str(verdict.get("verdict", "")),
        **_evidence_kwargs(evidence_vault, fid),
        actor_role=actor_role,
    )
    capped = dict(verdict)
    capped["veracity"] = assessment.to_dict()
    if state is FindingState.FALSE_POSITIVE:
        capped["verdict"] = "false_positive"
    elif state is FindingState.VERIFIED:
        capped["verdict"] = "verified"
    elif state is FindingState.CANDIDATE and str(verdict.get("verdict", "")).lower() == "verified":
        capped["verdict"] = "candidate"
        capped["reason"] = (f"VERIFIED ditahan gate veracity: "
                            f"{', '.join(assessment.blockers)}")
    return capped


def apply_verification(findings_db: Any, finding: Any, verdict: dict[str, Any],
                       evidence_vault: Any = None,
                       actor_role: str | None = None) -> FindingState:
    """Tulis hasil verifikasi ke `FindingsDB` lewat guard transisi."""
    state, assessment = decide_state(
        finding.state, str(verdict.get("verdict", "")),
        **_evidence_kwargs(evidence_vault, finding.id),
        actor_role=actor_role,
    )
    if state is not finding.state:
        findings_db.update_finding_state(finding.id, state)
    if state is FindingState.VERIFIED:
        # Item 4: hanya verifier independen yang merekam attestasi.
        att = attest_verification(
            finding_id=finding.id,
            actor_role=actor_role or "independent_verifier",
            verdict=str(verdict.get("verdict", "verified")),
            level=assessment.level,
        )
        finding.attestation = att.to_dict()
    return state
