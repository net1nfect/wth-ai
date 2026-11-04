"""Pilih skill yang relevan untuk satu instruksi (maksimal 5).

Deterministik: skor kata kunci pada id + deskripsi, potong di ambang, ambil
teratas. Tidak ada model, tidak ada embedding — supaya hasilnya bisa diuji dan
prompt tidak membengkak.
"""

from __future__ import annotations

import re

LIMIT = 5
MIN_SCORE = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "dan atau untuk dari yang dengan pada di ke ini itu apa apakah bagaimana "
    "tolong saya kita bisa test uji cari check the and for with".split()
)


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall((text or "").lower())
            if len(token) > 2 and token not in _STOP}


def score_skill(query_tokens: set[str], skill_id: str, description: str) -> int:
    haystack = _tokens(f"{skill_id} {description}")
    if not haystack:
        return 0
    overlap = query_tokens & haystack
    score = len(overlap)
    # id yang disebut utuh lebih kuat daripada kemiripan kata
    if skill_id.lower().replace("-", " ") in (description or "").lower():
        score += 1
    return score


def select_skills(user_input: str, skills, limit: int = LIMIT) -> list[str]:
    """`skills` = iterable objek ber-`id` dan `.description`."""
    query = _tokens(user_input)
    if not query:
        return []
    scored = [
        (score_skill(query, skill.id, skill.description or ""), skill.id)
        for skill in skills
    ]
    scored = [(score, sid) for score, sid in scored if score >= MIN_SCORE]
    # urut skor turun, lalu id agar stabil antar-run
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [sid for _, sid in scored[:limit]]