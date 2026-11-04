"""Antrean temuan yang terlihat.

Tidak ada pembuangan diam-diam: setiap temuan yang masuk antrean muncul di
snapshot, dan temuan yang tidak sempat dicoba tetap tercatat sebagai QUEUED.
"""

from __future__ import annotations

from wth_ai.redteam.models import AttemptState, Finding


class FindingQueue:
    def __init__(self, findings: list[Finding] | None = None) -> None:
        self._items: list[Finding] = list(findings or [])

    def add(self, finding: Finding) -> None:
        self._items.append(finding)

    def extend(self, findings: list[Finding]) -> None:
        self._items.extend(findings)

    def next(self, exclude: set[str] | None = None) -> Finding | None:
        """Temuan berikutnya yang masih perlu dicoba.

        `exclude` mencegah loop mengambil temuan yang sama dua kali: temuan
        UNPROVEN tetap "pending" (bisa dicoba ulang oleh operator/run lain),
        tapi run yang sedang berjalan sudah menghabiskannya.
        """
        skip = exclude or set()
        for item in self._items:
            if item.state in (AttemptState.QUEUED, AttemptState.UNPROVEN):
                if item.id not in skip:
                    return item
        return None

    def pending(self) -> list[Finding]:
        return [f for f in self._items
                if f.state in (AttemptState.QUEUED, AttemptState.UNPROVEN)]

    def all(self) -> list[Finding]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self.pending())