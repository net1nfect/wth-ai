"""`ClaimStore` — evidence-grounded claims (WU-114, ADR-007, DATA-MODEL.md §5).

Cousin pattern to `FindingsDB` (in-memory + flush to `SessionState.claims`).

Scope decision (found during implementation -- narrower than the full target
design in DATA-MODEL.md §5.1): the target sequencing has `Claim` created at
PLANNING time (before broker dispatch, `claim_id` pre-populated into
`ActionIntent.claim_ids`), with the broker APPENDING `evidence_refs` to the
already-existing claim during execution, and `EvidencePipeline` demoted to a
fallback role for orphaned evidence. That full flow needs `ActionIntent` to
actually carry `claim_ids` and the broker to actually append evidence on
execution -- neither exists yet (WU-102's broker skeleton doesn't touch
evidence at all; see its own scope decision). This WU delivers the `Claim`
data model + `ClaimStore` CRUD as a real, working, testable foundation --
the planner-time creation wiring, broker-append integration, and
`VerifierAgent` `captured_via` weighting are deferred to follow-up WUs once
the broker itself grows evidence-handling (a prerequisite this WU doesn't
have). `EVD-` prefix retirement (EvidencePipeline's own id scheme) is
NOT touched here either -- that's a behavior change to a different,
already-working component, out of scope for introducing a new one.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

from wth_ai.models import (
    Claim,
    FindingState,
    InvalidFindingStateTransitionError,
    finding_state_transition_allowed,
    utc_now,
)

_VALID_EVIDENCE_PREFIXES = ("TX:", "EV:")


class ClaimStore:
    """In-memory claims store."""

    def __init__(self) -> None:
        self.claims: dict[str, Claim] = {}

    def create_claim_from_hypothesis(self, data: dict[str, Any]) -> Claim:
        """Primary entry point (DATA-MODEL.md §5.1): called at PLANNING time,
        before any broker dispatch -- the resulting `claim_id` is meant to
        be threaded into `ActionIntent.claim_ids` by a future WU once the
        broker supports it."""
        cid = data.get("claim_id") or f"CLAIM-{uuid4().hex[:8]}"
        status_raw = data.get("verification_status", FindingState.HYPOTHESIS.value)
        status = (
            FindingState(status_raw)
            if status_raw in {s.value for s in FindingState}
            else FindingState.HYPOTHESIS
        )
        claim = Claim(
            claim_id=cid,
            text=data.get("text", data.get("title", "")),
            claim_type=data.get("claim_type", "observation"),
            vuln_class=data.get("vuln_class", ""),
            finding_id=data.get("finding_id", ""),
            task_id=data.get("task_id", ""),
            evidence_refs=list(data.get("evidence_refs", [])),
            confidence=float(data.get("confidence", 0.0)),
            verification_status=status,
            severity_hint=data.get("severity_hint", "info"),
            source=data.get("source", ""),
            captured_via=data.get("captured_via", "llm_assertion"),
        )
        self.claims[cid] = claim
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        return self.claims.get(claim_id)

    def append_evidence(self, claim_id: str, *refs: str) -> Claim | None:
        """Append evidence_refs to an EXISTING claim -- the future broker
        integration point (DATA-MODEL.md §5.1: "Broker lalu APPEND
        evidence_refs ke claim yang SUDAH ADA, tidak membuat claim baru").
        Refs without a valid TX:/EV: prefix are silently skipped (not
        raised -- matches GroundingFirewall's own recognized reference
        vocabulary, invalid refs just never satisfy grounding checks)."""
        claim = self.claims.get(claim_id)
        if not claim:
            return None
        for ref in refs:
            if ref.startswith(_VALID_EVIDENCE_PREFIXES) and ref not in claim.evidence_refs:
                claim.evidence_refs.append(ref)
        claim.updated_at = utc_now()
        return claim

    def update_verification_status(self, claim_id: str, status: FindingState | str) -> Claim | None:
        """WU-116: guarded by the same finding_state_transition_allowed()
        used by FindingsDB -- Claim.verification_status reuses FindingState,
        same illegal-flip risk once a caller wires this up (see its own
        docstring: currently zero real callers, foundation only)."""
        claim = self.claims.get(claim_id)
        if not claim:
            return None
        if isinstance(status, str):
            status = FindingState(status) if status in {s.value for s in FindingState} else claim.verification_status
        if not finding_state_transition_allowed(claim.verification_status, status):
            if os.environ.get("WTH_TEST_MODE") == "1" or os.environ.get("WTH_STRICT_SM") == "1":
                raise InvalidFindingStateTransitionError(
                    f"Illegal claim verification_status transition for {claim_id}: "
                    f"{claim.verification_status.value} -> {status.value}"
                )
            return claim  # clamp: unchanged, not corrupted, not pretended-successful
        claim.verification_status = status
        claim.updated_at = utc_now()
        return claim

    def list_claims(self, *, finding_id: str | None = None, task_id: str | None = None) -> list[Claim]:
        items = list(self.claims.values())
        if finding_id:
            items = [c for c in items if c.finding_id == finding_id]
        if task_id:
            items = [c for c in items if c.task_id == task_id]
        return sorted(items, key=lambda c: c.created_at)

    def to_dict(self) -> dict[str, Any]:
        return {"claims": [c.to_dict() for c in self.claims.values()]}

    def load_from_dict(self, data: dict[str, Any]) -> None:
        for c in data.get("claims", []):
            self.create_claim_from_hypothesis(c)
