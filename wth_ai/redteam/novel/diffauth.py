"""Matriks otorisasi diferensial.

Deterministik: keputusan diambil dari pasangan request/response, bukan narasi
model. LLM hanya boleh menamai hasilnya nanti.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Probe:
    identity: str
    object_id: str
    method: str
    status: int


@dataclass
class DiffResult:
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"candidates": list(self.candidates)}


def analyze(probes: list[Probe]) -> DiffResult:
    by_key: dict[tuple[str, str], dict[str, int]] = {}
    for probe in probes:
        by_key.setdefault((probe.object_id, probe.method.upper()), {})[
            probe.identity] = probe.status
    result = DiffResult()
    for (object_id, method), statuses in by_key.items():
        owner_status = statuses.get("A")
        other_status = statuses.get("B")
        if owner_status == 200 and other_status == 200:
            result.candidates.append({
                "object_id": object_id,
                "method": method,
                "owner_status": owner_status,
                "other_status": other_status,
                "signal": "identitas lain mendapat 200 pada objek yang sama",
            })
    return result