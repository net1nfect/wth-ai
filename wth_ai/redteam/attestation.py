"""Independent verification attestation (item 4 of rencana Strix).

Mirror Strix: penemuan tidak pernah mengonfirmasi dirinya sendiri. Hanya
verifier independen (tanpa mewarisi konteks discovery) yang boleh menaikkan
CANDIDATE/REPRODUCED -> VERIFIED.

Non-destruktif: `actor_role=None` mempertahankan perilaku lama. Rol baru
dikenakan eksplisit dari call site, jadi default run tidak berubah.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class VerifierRole(str, Enum):
    DISCOVERER = "discoverer"
    INDEPENDENT = "independent_verifier"


def can_promote_to_verified(actor_role: str | None) -> bool:
    """Discoverer tidak pernah self-confirm; sisanya (termasuk legacy None)
    boleh menaikkan ke VERIFIED."""
    return actor_role != VerifierRole.DISCOVERER.value


@dataclass(frozen=True)
class Attestation:
    finding_id: str
    verifier_role: str
    verdict: str
    level: str
    isolated_from_discovery: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "verifier_role": self.verifier_role,
            "verdict": self.verdict,
            "level": self.level,
            "isolated_from_discovery": self.isolated_from_discovery,
            "detail": self.detail,
        }


def attest_verification(
    *,
    finding_id: str,
    actor_role: str,
    verdict: str,
    level: str,
    isolated_context: bool = True,
    detail: str = "",
) -> Attestation:
    """Rekam attestasi verifikasi independen.

    Discoverer dilarang mengattestasi verifikasi independen atas temuannya
    sendiri: itu self-confirm, dan tidak pernah diizinkan.
    """
    if actor_role == VerifierRole.DISCOVERER.value:
        raise ValueError("discoverer tidak dapat mengattestasi verifikasi independen")
    return Attestation(
        finding_id=finding_id,
        verifier_role=actor_role,
        verdict=verdict,
        level=level,
        isolated_from_discovery=bool(isolated_context),
        detail=detail,
    )
