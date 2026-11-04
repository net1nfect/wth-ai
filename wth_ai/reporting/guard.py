"""Satu pintu untuk semua teks yang keluar sebagai laporan.

`veracity.py` sudah punya `sanitize_claims()` dan `guard_note()`; modul ini tidak
menggantikannya, hanya memastikan setiap jalur laporan memanggil keduanya
dengan urutan yang sama.
"""

from __future__ import annotations

from wth_ai.veracity import guard_note, sanitize_claims


def guard_report_text(text: str) -> str:
    safe, violations = sanitize_claims(text)
    note = guard_note(violations)
    return f"{safe}\n\n{note}" if note else safe