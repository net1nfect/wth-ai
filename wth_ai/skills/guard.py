"""Scan skill packs before install."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DANGEROUS = (
    re.compile(r"rm\s+-rf\s+/", re.I),
    re.compile(r"curl\s+.*\|\s*bash", re.I),
    re.compile(r"eval\s*\(", re.I),
)


@dataclass
class GuardReport:
    blocked: bool = False
    reason: str = ""
    warnings: list[str] | None = None


def scan_skill_path(path: Path) -> GuardReport:
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        return GuardReport(blocked=True, reason="missing SKILL.md")
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    warnings: list[str] = []
    for pat in DANGEROUS:
        if pat.search(text):
            return GuardReport(blocked=True, reason=f"dangerous pattern: {pat.pattern}")
    if len(text) > 500_000:
        warnings.append("very large SKILL.md")
    return GuardReport(blocked=False, warnings=warnings)
