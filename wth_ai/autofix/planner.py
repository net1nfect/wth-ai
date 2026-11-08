"""Rencana patch untuk satu temuan — tidak pernah mengarang perbaikan.

Satu-satunya sumber patch adalah blok ```diff``` yang tersimpan di evidence
temuan (mis. dari runner SAST). Temuan tanpa saran mesin-usable ditolak dengan
`AutofixUnavailable`: menebak perbaikan keamanan lalu menerapkannya otomatis
adalah cara paling cepat menambah kerentanan baru.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DIFF_FENCE = re.compile(r"```diff\n(.*?)```", re.DOTALL)
_TARGET = re.compile(r"^\+\+\+ b/(\S+)$", re.MULTILINE)

class AutofixUnavailable(RuntimeError):
    """Temuan tidak punya saran patch yang bisa diterapkan."""

@dataclass(frozen=True)
class Patch:
    finding_id: str
    path: str
    diff: str
    rationale: str = ""

def _path_from(diff: str, fallback: str) -> str:
    match = _TARGET.search(diff)
    if match:
        return match.group(1)
    return fallback

def plan_fix(finding: Any) -> Patch:
    """Patch dari evidence temuan; tanpa blok diff → `AutofixUnavailable`."""
    for item in getattr(finding, "evidence", None) or []:
        text = str(item)
        match = _DIFF_FENCE.search(text)
        if not match:
            continue
        diff = match.group(1)
        fallback = str(getattr(finding, "target", "") or "")
        path = _path_from(diff, fallback)
        if not path:
            continue
        return Patch(
            finding_id=str(getattr(finding, "id", "")),
            path=path,
            diff=diff,
            rationale=str(getattr(finding, "hypothesis", "") or ""),
        )
    raise AutofixUnavailable(
        f"temuan {getattr(finding, 'id', '?')} tidak punya saran patch "
        "(blok ```diff``` di evidence)"
    )
