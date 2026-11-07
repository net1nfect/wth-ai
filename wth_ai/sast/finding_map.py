"""Pemetaan keluaran opengrep ke model temuan red team.

Temuan statis selalu `candidate`. Alasannya bukan kehati-hatian berlebihan:
`veracity.assess_claim` mensyaratkan request/response tertangkap, minimal satu
reproduksi, dan kontrol negatif. Analisis kode tidak punya ketiganya, jadi
menaikkan statusnya akan membuat bundel bukti berbohong — pembaca tidak akan
menemukan request yang membuktikan klaim itu.
"""

from __future__ import annotations

import hashlib
from typing import Any

from wth_ai.redteam.models import AttemptState, Finding

_SEVERITY_MAP = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


def to_finding(raw: dict[str, Any], *, target_name: str) -> Finding:
    rule = str(raw.get("rule_id", "") or "unknown")
    path = str(raw.get("path", ""))
    line = int(raw.get("line", 0) or 0)
    severity = _SEVERITY_MAP.get(str(raw.get("severity", "")).upper(), "low")
    message = str(raw.get("message", "")) or rule
    snippet = str(raw.get("snippet", ""))[:400]

    return Finding(
        id=_finding_id(rule, path, line),
        hypothesis=f"{message} ({path}:{line})",
        vuln_class=rule,
        target=target_name,
        severity=severity,
        state=AttemptState.CANDIDATE,
        evidence=[f"static:opengrep:{rule}:{path}:{line}\n{snippet}"],
        provenance="SAST_OPENGREP",
        origin="static",
    )


def _finding_id(rule: str, path: str, line: int) -> str:
    """Id stabil: scan ulang atas pohon yang sama menghasilkan id yang sama."""
    digest = hashlib.sha256(f"{rule}:{path}:{line}".encode()).hexdigest()[:10]
    return f"SAST-{digest}"
