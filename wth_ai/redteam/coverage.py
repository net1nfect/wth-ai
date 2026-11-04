"""Coverage: permukaan yang benar-benar DICOBA vs yang ditemukan.

Tanpa ini, "12 temuan" tidak bisa dibedakan dari "12 temuan karena baru 12 yang
dicoba". Persentase ini masuk laporan, selalu dengan penyebutnya.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Coverage:
    surfaces_total: int
    surfaces_attempted: int
    findings_confirmed: int

    @property
    def attempted_ratio(self) -> float:
        if self.surfaces_total <= 0:
            return 0.0
        return round(self.surfaces_attempted / self.surfaces_total, 4)

    @property
    def hit_ratio(self) -> float:
        if self.surfaces_attempted <= 0:
            return 0.0
        return round(self.findings_confirmed / self.surfaces_attempted, 4)

    def to_dict(self) -> dict:
        return {
            "surfaces_total": self.surfaces_total,
            "surfaces_attempted": self.surfaces_attempted,
            "findings_confirmed": self.findings_confirmed,
            "attempted_ratio": self.attempted_ratio,
            "hit_ratio": self.hit_ratio,
        }

    def line(self) -> str:
        return (f"coverage: {self.surfaces_attempted}/{self.surfaces_total} permukaan "
                f"dicoba ({self.attempted_ratio:.0%}) · "
                f"{self.findings_confirmed} terkonfirmasi "
                f"({self.hit_ratio:.0%} dari yang dicoba)")