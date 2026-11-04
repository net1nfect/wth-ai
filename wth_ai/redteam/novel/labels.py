"""Label temuan: apa yang bisa diklaim, dan apa yang tidak.

Aturan tunggal: NOVEL tidak boleh menyebut CVE. Kalau menyebut, sitasi harus
divalidasi KB (dan kalau tidak ada di KB, gagal keras).
"""

from __future__ import annotations

from enum import Enum

from wth_ai.kb.cite import cited_cves


class Provenance(str, Enum):
    CVE_BACKED = "CVE_BACKED"
    CWE_PATTERN = "CWE_PATTERN"
    NOVEL = "NOVEL"


CONFIRMED_NOVEL = "CONFIRMED-NOVEL"
CANDIDATE_NOVEL = "CANDIDATE-NOVEL"
KNOWN_CVE = "KNOWN ({cve})"
KNOWN_CWE = "KNOWN ({cwe})"

NOVEL_DISCLAIMER = (
    "Status novel berarti tidak ditemukan pada basis data CVE {built_at} "
    "({total} record). Klaim kebaruan terhadap seluruh dunia tidak dapat "
    "diverifikasi oleh tool ini."
)


def assert_novel_has_no_cve(text: str) -> None:
    found = cited_cves(text)
    if found:
        raise ValueError(
            "klaim NOVEL tidak boleh menyebut CVE: " + ", ".join(found))


def label(provenance: Provenance, *, confirmed: bool, cve: str = "",
          cwe: str = "") -> str:
    if provenance is Provenance.NOVEL:
        return CONFIRMED_NOVEL if confirmed else CANDIDATE_NOVEL
    if provenance is Provenance.CVE_BACKED:
        return KNOWN_CVE.format(cve=cve or "?")
    return KNOWN_CWE.format(cwe=cwe or "?")