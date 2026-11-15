"""Arena regression harness — kasus yang hilang = FAIL, bukan segasgasan.

Anti-overclaim: setiap fitur baru yang menyentuh executor yang terdaftar
VULN_EXECUTORS menjawab pertanyaan "apakah masih menemukan bug nyata?".
Kasus nuclei (butuh binary eksternal) hanya dijalankan bila binary tersedia —
jujur dihitung SKIP, bukan gagal.
"""

from __future__ import annotations

import os
import shutil

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.arena.runner import ARENA_CASES, run_arena  # noqa: E402


def test_arena_all_cases_detected():
    cases = [
        c for c in ARENA_CASES
        # nuclei lambat & perlu binary — hanya dijalankan kalau benar-benar
        # tersedia dan env tidak menyuruh hemat waktu (WTH_ARENA_FAST=1).
        if not (c.vuln_type == "nuclei"
                and (os.environ.get("WTH_ARENA_FAST") == "1"
                     or shutil.which(c.requires or "nuclei") is None))
    ]
    report = run_arena(cases)
    if report.failed:
        details = "; ".join(
            f"{r.case_id}={r.detail[:80]} evidence={r.evidence[:2]}"
            for r in report.failed
        )
        raise AssertionError(f"Arena regresi — kemampuan turun: {details}")
    # Kasus SKIP tidak dihitung sebagai pass; semua non-skip WAJIB pass.
    non_skip = [r for r in report.results if r.outcome != "SKIP"]
    assert report.passed == len(non_skip)
    assert len(non_skip) >= 8, f"arena cuma {len(non_skip)} kasus non-skip — registry hilang?"
