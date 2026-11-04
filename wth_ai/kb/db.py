"""Skema KB + koneksi. Satu tabel CVE, satu indeks FTS5, satu tabel meta."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cve (
    cve_id TEXT PRIMARY KEY,
    published TEXT NOT NULL DEFAULT '',
    cvss REAL,
    kev INTEGER NOT NULL DEFAULT 0,
    epss REAL,
    assessment_ready INTEGER NOT NULL DEFAULT 0,
    cwe TEXT NOT NULL DEFAULT '',
    product TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS cve_fts USING fts5(
    cve_id UNINDEXED, summary, product, cwe
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def upsert_cve(conn: sqlite3.Connection, record: dict) -> None:
    """Masukkan/perbarui satu record + baris FTS-nya."""
    cve_id = str(record["cve_id"]).upper()
    conn.execute(
        "INSERT INTO cve(cve_id, published, cvss, kev, epss, assessment_ready,"
        " cwe, product, summary) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(cve_id) DO UPDATE SET published=excluded.published,"
        " cvss=excluded.cvss, kev=excluded.kev, epss=excluded.epss,"
        " assessment_ready=excluded.assessment_ready, cwe=excluded.cwe,"
        " product=excluded.product, summary=excluded.summary",
        (cve_id, record.get("published", ""), record.get("cvss"),
         1 if record.get("kev") else 0, record.get("epss"),
         1 if record.get("assessment_ready") else 0,
         record.get("cwe", ""), record.get("product", ""),
         record.get("summary", "")),
    )
    conn.execute("DELETE FROM cve_fts WHERE cve_id=?", (cve_id,))
    conn.execute(
        "INSERT INTO cve_fts(cve_id, summary, product, cwe) VALUES(?,?,?,?)",
        (cve_id, record.get("summary", ""), record.get("product", ""),
         record.get("cwe", "")),
    )
    conn.commit()


def count(conn: sqlite3.Connection) -> tuple[int, int]:
    """(total record, record siap-assessment)."""
    total = conn.execute("SELECT COUNT(*) AS c FROM cve").fetchone()["c"]
    ready = conn.execute(
        "SELECT COUNT(*) AS c FROM cve WHERE assessment_ready=1").fetchone()["c"]
    return total, ready