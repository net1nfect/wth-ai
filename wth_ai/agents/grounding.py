"""Grounding firewall — anti-hallucination for agent claims."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CLAIM_PATTERNS = re.compile(
    r"(?i)\b(verified|found\s+(a\s+)?vuln|critical\s+vuln|exploited|confirmed\s+xss|confirmed\s+idor|"
    r"confirmed\s+ssrf|rce\s+achieved|sql\s+injection\s+confirmed)\b",
)
TX_REF = re.compile(r"\[TX:[A-Za-z0-9_-]+\]")
EV_REF = re.compile(r"\[EV:[A-Za-z0-9_-]+\]")


@dataclass
class GroundingResult:
    text: str
    score: float
    violations: list[str] = field(default_factory=list)
    grounded: bool = True


class GroundingFirewall:
    """Validate and soften ungrounded vulnerability claims."""

    def __init__(self, *, min_score: float = 0.5) -> None:
        self.min_score = min_score

    def has_evidence_refs(self, text: str) -> bool:
        return bool(TX_REF.search(text) or EV_REF.search(text))

    def validate_claims(
        self,
        text: str,
        *,
        transcript_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> GroundingResult:
        violations: list[str] = []
        score = 1.0
        has_refs = self.has_evidence_refs(text)
        tx_ids = transcript_ids or []
        ev_ids = evidence_ids or []

        for match in CLAIM_PATTERNS.finditer(text):
            claim = match.group(0)
            if not has_refs and not tx_ids and not ev_ids:
                violations.append(f"Ungrounded claim: {claim}")
                score -= 0.35

        if violations:
            text = self.filter(text)
            grounded = score >= self.min_score
        else:
            grounded = True

        return GroundingResult(text=text, score=max(0.0, score), violations=violations, grounded=grounded)

    def filter(self, text: str) -> str:
        """Prefix ungrounded strong claims with hypothesis marker."""
        if self.has_evidence_refs(text):
            return text

        def repl(m: re.Match[str]) -> str:
            return f"HIPOTESIS (belum dibuktikan — butuh [TX:...]): {m.group(0)}"

        return CLAIM_PATTERNS.sub(repl, text)

    def append_tx_citation(self, text: str, transcript_id: str) -> str:
        if TX_REF.search(text):
            return text
        return f"{text}\n\nBukti: [TX:{transcript_id}]"
