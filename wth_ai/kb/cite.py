"""Validator sitasi: setiap CVE yang disebut harus ada di DB.

Gagal keras, bukan peringatan: laporan yang menyebut CVE karangan merusak satu-
satunya hal yang membuat laporan ini berguna.
"""

from __future__ import annotations

import re
import sqlite3

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)


class UngroundedCitation(RuntimeError):
    """CVE disebut tapi tidak ada di basis data."""


def validate_citations(text: str, conn: sqlite3.Connection) -> list[str]:
    found = sorted({match.group(0).upper() for match in CVE_RE.finditer(text or "")})
    missing: list[str] = []
    for cve in found:
        row = conn.execute("SELECT 1 FROM cve WHERE cve_id=?", (cve,)).fetchone()
        if row is None:
            missing.append(cve)
    if missing:
        raise UngroundedCitation("CVE tidak ada di basis data: " + ", ".join(missing))
    return found


def cited_cves(text: str) -> list[str]:
    """CVE yang disebut, tanpa menyentuh DB (untuk test/label)."""
    return sorted({match.group(0).upper() for match in CVE_RE.finditer(text or "")})