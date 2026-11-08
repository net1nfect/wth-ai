"""Completion gate (fitur Strix): menolak finalisasi sampai semua temuan selesai.

Meniru `core/completion.py` Strix. Status = COMPLETED / PARTIAL / BLOCKED.
Blocker klasik:
- `unresolved_findings` — state yang belum dispositioned (bukan terminal).
- `unreported_verified_findings` — VERIFIED/REPRODUCED yang belum dilaporkan.
- `cleanup_blocked` — pembersihan sandbox tak selesai.
- `evidence_invalid` — bukti gagal verifikasi (mis. rantai hash putus).
- `terminal_mutation_conflict` — mutasi pada state terminal.

Non-destruktif: gate ini dipanggil eksplisit di `finish_scan`/exit-code;
tidak mengubah perilaku default run selama tidak diaktifkan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Mapping

# State terminal/disposed yang bukan blocker laporan.
_DISPOSED_STATES = {
    "FALSE_POSITIVE",
    "ACCEPTED_RISK",
    "FIXED",
    "RETEST_PASSED",
}

# State yang sudah punya bukti dan WAJIB dilaporkan sebelum close.
_NEEDS_REPORT_STATES = {
    "VERIFIED",
    "REPRODUCED",
    "REPORTABLE",
}


class CompletionStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass
class CompletionResult:
    status: CompletionStatus
    unresolved_findings: int = 0
    unreported_verified_findings: int = 0
    cleanup_blocked: bool = False
    evidence_invalid: bool = False
    terminal_mutation_conflict: bool = False
    blockers: List[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, CompletionStatus):
            return self.status is other
        if isinstance(other, str):
            return self.status.value == other
        return NotImplemented


def evaluate_completion(
    findings: List[Mapping],
    *,
    cleanup_blocked: bool = False,
    evidence_invalid: bool = False,
    terminal_mutation_conflict: bool = False,
) -> CompletionResult:
    """Evaluasi kesiapan finalisasi. `findings` = list dict temuan aktif."""
    unresolved = 0
    unreported_verified = 0
    for f in findings:
        state = str(f.get("state", "")).upper()
        reported = bool(f.get("reported"))
        if state in _NEEDS_REPORT_STATES and not reported:
            unreported_verified += 1
        # Resolved: state disposed, atau state-butuh-laporan yang sudah dilaporkan.
        if not (state in _DISPOSED_STATES or (state in _NEEDS_REPORT_STATES and reported)):
            unresolved += 1

    blockers: List[str] = []
    if cleanup_blocked:
        blockers.append("cleanup_blocked")
    if evidence_invalid:
        blockers.append("evidence_invalid")
    if terminal_mutation_conflict:
        blockers.append("terminal_mutation_conflict")
    if unreported_verified:
        blockers.append("unreported_verified_findings")
    if unresolved and not unreported_verified and not blockers:
        # Masih ada yang unresolved tapi semua yang perlu laporan sudah dilaporkan.
        blockers.append("unresolved_findings")

    if blockers:
        return CompletionResult(
            status=CompletionStatus.BLOCKED,
            unresolved_findings=unresolved,
            unreported_verified_findings=unreported_verified,
            cleanup_blocked=cleanup_blocked,
            evidence_invalid=evidence_invalid,
            terminal_mutation_conflict=terminal_mutation_conflict,
            blockers=blockers,
        )
    return CompletionResult(
        status=CompletionStatus.COMPLETED,
        unresolved_findings=unresolved,
    )
