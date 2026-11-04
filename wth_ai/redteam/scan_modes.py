"""Tiga kedalaman scan, dengan angka yang bisa diuji.

Bukan pemasaran: setiap mode menetapkan berapa kali satu temuan dicoba, berapa
langkah maksimum, dan profile recon mana yang dipakai. Angka-angka ini masuk
`run.json` supaya hasil bisa dibandingkan antar-run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScanMode:
    key: str
    max_attempts: int
    max_steps: int
    recon_profile: str
    note: str


SCAN_MODES: dict[str, ScanMode] = {
    "quick": ScanMode("quick", 1, 25, "budget", "cek cepat, satu percobaan per temuan"),
    "standard": ScanMode("standard", 3, 100, "default", "seimbang, tiga percobaan"),
    "deep": ScanMode("deep", 5, 400, "high-quality", "menyeluruh, lima percobaan"),
}
DEFAULT_SCAN_MODE = "deep"


def get_mode(key: str | None) -> ScanMode:
    if not key:
        return SCAN_MODES[DEFAULT_SCAN_MODE]
    if key not in SCAN_MODES:
        raise ValueError(f"scan-mode tidak dikenal: {key} "
                         f"(pilih: {', '.join(SCAN_MODES)})")
    return SCAN_MODES[key]