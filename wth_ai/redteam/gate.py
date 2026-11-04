"""Kontrak exit code untuk CI: 0 bersih, 1 error, 2 ada temuan.

Severity yang tidak dikenal dihitung sebagai temuan — gate tidak boleh lolos
hanya karena string-nya aneh.
"""

from __future__ import annotations

from wth_ai.redteam.models import AttemptState, Finding

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _rank(severity: str) -> int:
    return SEVERITY_ORDER.get((severity or "").lower(), 4)  # tak dikenal = paling berat


def exit_code(findings: list[Finding], fail_on: str | None = None) -> int:
    confirmed = [f for f in findings if f.state is AttemptState.CONFIRMED]
    if not confirmed:
        return 0
    if fail_on is None:
        return 2
    threshold = SEVERITY_ORDER.get(fail_on.lower())
    if threshold is None:
        raise ValueError(f"severity tidak dikenal: {fail_on}")
    return 2 if any(_rank(f.severity) >= threshold for f in confirmed) else 0