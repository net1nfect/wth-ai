"""Model loop red team.

`AttemptState` adalah status SATU percobaan pada SATU temuan. Ia sengaja
terpisah dari `models.FindingState` (lifecycle temuan dalam engagement):
mencampur keduanya membuat dua mesin status saling bertentangan. Pemetaan
dilakukan di satu tempat: `to_finding_state()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class AttemptState(str, Enum):
    QUEUED = "queued"
    ATTEMPTING = "attempting"
    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"
    REFUTED = "refuted"
    UNPROVEN = "unproven"


_TERMINAL = {AttemptState.CONFIRMED, AttemptState.REFUTED, AttemptState.CANDIDATE}
_ALLOWED: dict[AttemptState, set[AttemptState]] = {
    AttemptState.QUEUED: {AttemptState.ATTEMPTING},
    AttemptState.ATTEMPTING: {AttemptState.CONFIRMED, AttemptState.CANDIDATE,
                              AttemptState.REFUTED, AttemptState.UNPROVEN},
    AttemptState.UNPROVEN: {AttemptState.ATTEMPTING},
    AttemptState.CONFIRMED: set(),
    AttemptState.CANDIDATE: {AttemptState.ATTEMPTING},
    AttemptState.REFUTED: set(),
}


class InvalidAttemptTransition(RuntimeError):
    """Transisi status tidak boleh terjadi."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Finding:
    id: str
    hypothesis: str
    vuln_class: str = "unknown"
    target: str = ""
    severity: str = "medium"
    state: AttemptState = AttemptState.QUEUED
    attempts: int = 0
    evidence: list[str] = field(default_factory=list)
    attestation: dict = field(default_factory=dict)
    provenance: str = "CWE_PATTERN"
    # `dynamic` = ada request/response; `static` = dari analisis kode (SAST).
    # Temuan statis tidak pernah bisa naik ke CONFIRMED: bukti runtime-nya nol.
    origin: str = "dynamic"
    chain_id: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def transition(self, new: AttemptState) -> AttemptState:
        if new is self.state:
            return self.state
        if new not in _ALLOWED[self.state]:
            raise InvalidAttemptTransition(
                f"{self.id}: {self.state.value} -> {new.value} tidak diizinkan"
            )
        self.state = new
        self.updated_at = _now()
        return self.state

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL

    def to_finding_state(self) -> str:
        """Pemetaan ke lifecycle `models.FindingState` (dipakai FindingsDB)."""
        return {
            AttemptState.CONFIRMED: "verified",
            AttemptState.CANDIDATE: "candidate",
            AttemptState.REFUTED: "false_positive",
            AttemptState.UNPROVEN: "needs_evidence",
            AttemptState.ATTEMPTING: "hypothesis",
            AttemptState.QUEUED: "hypothesis",
        }[self.state]

    @classmethod
    def from_dict(cls, data: dict) -> "Finding":
        state = data.get("state", "queued")
        try:
            state_value = AttemptState(state)
        except ValueError:
            state_value = AttemptState.QUEUED
        attestation = dict(data.get("attestation") or {})
        # P0-2: `state: confirmed` di JSON adalah klaim dari luar, bukan bukti.
        # Hanya attestation veracity yang boleh mengangkatnya.
        if state_value is AttemptState.CONFIRMED and (
            str(attestation.get("level", "")).lower() != "confirmed"
        ):
            state_value = AttemptState.CANDIDATE
        return cls(
            id=data.get("id", ""),
            hypothesis=data.get("hypothesis", ""),
            vuln_class=data.get("vuln_class", "unknown"),
            target=data.get("target", ""),
            severity=data.get("severity", "medium"),
            state=state_value,
            attempts=int(data.get("attempts", 0) or 0),
            evidence=list(data.get("evidence") or []),
            attestation=attestation,
            provenance=data.get("provenance", "CWE_PATTERN"),
            chain_id=data.get("chain_id", ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "hypothesis": self.hypothesis,
            "vuln_class": self.vuln_class,
            "target": self.target,
            "severity": self.severity,
            "state": self.state.value,
            "attempts": self.attempts,
            "evidence": list(self.evidence),
            "attestation": dict(self.attestation),
            "provenance": self.provenance,
            "chain_id": self.chain_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }