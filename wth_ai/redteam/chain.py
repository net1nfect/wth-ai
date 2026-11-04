"""Rantai temuan: informasi dari satu temuan menjadi konteks temuan berikutnya."""

from __future__ import annotations

from dataclasses import dataclass, field

from wth_ai.redteam.models import AttemptState, Finding

#: Nilai yang diwariskan ke temuan turunan.
CHAIN_KEYS = ("credentials", "paths", "headers", "tokens", "ids")


@dataclass
class Chain:
    id: str
    root: str
    links: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "root": self.root, "links": list(self.links)}


def derive_chain(confirmed: list[Finding], *, next_index: int = 1) -> list[Chain]:
    """Bangun rantai dari temuan CONFIRMED yang memberi informasi baru."""
    chains: list[Chain] = []
    for offset, finding in enumerate(confirmed):
        if finding.state is not AttemptState.CONFIRMED:
            continue
        context = {k: v for k, v in finding.attestation.items() if k in CHAIN_KEYS}
        if not context:
            continue
        chains.append(Chain(
            id=f"C-{next_index + offset:03d}",
            root=finding.id,
            links=[{"finding": finding.id, "context": context}],
        ))
    return chains