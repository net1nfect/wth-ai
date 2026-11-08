"""Menerjemahkan hasil percobaan menjadi status, lewat veracity.assess_claim.

Aturan yang tidak boleh dilanggar: CONFIRMED hanya bisa keluar dari
`assess_claim(level=confirmed)`. Tidak ada jalan pintas "kelihatannya rentan".
"""

from __future__ import annotations

from wth_ai.redteam.executor import AttemptOutcome
from wth_ai.redteam.models import AttemptState
from wth_ai.veracity import (LEVEL_CANDIDATE, LEVEL_CONFIRMED, LEVEL_REFUTED,
                             assess_claim)


def classify(outcome: AttemptOutcome) -> tuple[AttemptState, dict]:
    assessment = assess_claim(
        has_request_response=bool(outcome.request_response),
        reproductions=outcome.reproductions,
        controls=outcome.controls,
        tool_backed=outcome.tool_backed,
        refuted_by_control=outcome.refuted_by_control,
    )
    state = {
        LEVEL_CONFIRMED: AttemptState.CONFIRMED,
        LEVEL_CANDIDATE: AttemptState.CANDIDATE,
        LEVEL_REFUTED: AttemptState.REFUTED,
    }.get(assessment.level, AttemptState.UNPROVEN)
    return state, assessment.to_dict()


def enforce_scope(findings: list, *, scope_authorized: bool) -> list:
    """Turunkan CONFIRMED ke CANDIDATE bila tidak ada scope card ber-izin.

    P0-2: bundel bukti sebelumnya menulis `authorized: true` sebagai literal,
    jadi CONFIRMED yang keluar dari bundel itu tidak pernah menunjuk izin yang
    benar-benar ada. Veracity menjawab "apakah buktinya cukup"; fungsi ini
    menjawab "apakah ada izin untuk mengklaimnya".
    """
    if scope_authorized:
        return findings
    for finding in findings:
        if finding.state is AttemptState.CONFIRMED:
            finding.state = AttemptState.CANDIDATE
    return findings