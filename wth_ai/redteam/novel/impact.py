"""impact = 25*exploitability + 25*blast_radius + 20*data_sensitivity
          + 15*reproducibility + 15*chain_value  (input 0..1)"""

from __future__ import annotations

WEIGHTS = {"exploitability": 25, "blast_radius": 25, "data_sensitivity": 20,
           "reproducibility": 15, "chain_value": 15}


def impact_score(**factors: float) -> tuple[int, str]:
    total = sum(WEIGHTS[key] * float(factors.get(key, 0.0)) for key in WEIGHTS)
    score = int(round(total))
    if score >= 90:
        band = "CRITICAL"
    elif score >= 75:
        band = "HIGH"
    elif score >= 55:
        band = "MEDIUM"
    else:
        band = "LOW"
    return score, band


def weights_line() -> str:
    return ("impact = " + " + ".join(f"{w}*{k}" for k, w in WEIGHTS.items())
            + "  (faktor 0..1)")