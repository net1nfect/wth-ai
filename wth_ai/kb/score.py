"""Skor prioritas deterministik.

priority = 0.35*cvss_norm + 0.30*kev + 0.20*epss + 0.15*exposure

Bobot sengaja dicetak di laporan supaya angkanya bisa dibantah, bukan dipercaya
begitu saja.
"""

from __future__ import annotations

WEIGHTS = {"cvss": 0.35, "kev": 0.30, "epss": 0.20, "exposure": 0.15}


def priority(cvss: float | None, kev: bool, epss: float | None,
             exposure: float) -> float:
    cvss_norm = (cvss or 0.0) / 10.0
    score = (
        WEIGHTS["cvss"] * cvss_norm
        + WEIGHTS["kev"] * (1.0 if kev else 0.0)
        + WEIGHTS["epss"] * float(epss or 0.0)
        + WEIGHTS["exposure"] * float(exposure)
    )
    return round(score, 4)


def weights_line() -> str:
    return ("priority = " + " + ".join(f"{weight:.2f}*{name}"
                                        for name, weight in WEIGHTS.items()))