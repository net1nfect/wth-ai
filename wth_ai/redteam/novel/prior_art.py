"""Sebelum menyebut novel, cari kemiripan di KB.

Kalau jelas sama, turunkan ke CVE_BACKED dan sitasi CVE-nya. Ini yang mencegah
"menemukan" BOLA yang sudah ada CVE-nya bertahun-tahun lalu.
"""

from __future__ import annotations

import sqlite3

from wth_ai.redteam.novel.labels import Provenance


def _fts_query(text: str) -> str:
    """FTS5 MATCH tidak menerima tanda baca bebas; ambil token kata saja."""
    tokens = [token for token in
              "".join(ch if ch.isalnum() else " " for ch in text).split()
              if len(token) > 2]
    return " OR ".join(tokens[:8])


def search_similar(conn: sqlite3.Connection, *, cwe: str = "",
                   product: str = "", text: str = "",
                   limit: int = 5) -> list[dict]:
    query = _fts_query(" ".join(part for part in (product, cwe, text[:120]) if part))
    if not query:
        return []
    rows = conn.execute(
        "SELECT cve_id, summary, product FROM cve_fts "
        "WHERE cve_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def resolve_provenance(conn: sqlite3.Connection, *, cwe: str = "",
                       product: str = "", text: str = "") -> tuple[Provenance, list[dict]]:
    hits = search_similar(conn, cwe=cwe, product=product, text=text)
    if hits:
        return Provenance.CVE_BACKED, hits
    return Provenance.NOVEL, []