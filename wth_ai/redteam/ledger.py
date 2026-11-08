"""Hash-chain quality ledger — source of truth riwayat bukti lintas-run.

Diadaptasi dari konsep `QualityStateService` Strix (fork workbench) dan
menyejajarkan dengan doktrin bukti WTH-AI yang sudah ada (audit chain + manifest
sha256 di `bundle.py`). Principles:

- SQLite WAL adalah satu-satunya mutable source of truth; setiap mutasi append-only
  + atomik (BEGIN IMMEDIATE → idempotency check → append → update revision → commit).
- Setiap event membawa `previous_event_hash`; hash = sha256(prev + payload), sehingga
  tamper pada byte mana pun memutus rantai dan `verify_chain()` gagal.
- `idempotency_key` membuat replay deterministik: key sama + payload sama → event
  tersimpan dikembalikan; key sama + payload beda → `IdempotencyConflictError`.
- Snapshot baca-only memakai salinan privat + `PRAGMA query_only=ON`; ia tidak pernah
  membuka DB hidup dalam mode tulis.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    sequence           INTEGER PRIMARY KEY,
    event_id           TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    object_type        TEXT NOT NULL DEFAULT '',
    object_id          TEXT NOT NULL DEFAULT '',
    actor_id           TEXT NOT NULL DEFAULT '',
    actor_role         TEXT NOT NULL DEFAULT '',
    idempotency_key    TEXT NOT NULL UNIQUE,
    request_hash        TEXT,
    request_json        TEXT,
    result_json         TEXT,
    previous_event_hash TEXT NOT NULL,
    hash               TEXT NOT NULL,
    ledger_revision    INTEGER NOT NULL,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

ZERO_HASH = "0" * 64


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(payload: Any) -> str:
    return json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)


class IdempotencyConflictError(Exception):
    """idempotency_key dipakai ulang dengan payload berbeda."""


class Ledger:
    """Append-only hash-chain ledger di SQLite WAL."""

    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            self._conn = self._open_readonly_copy()
        else:
            self._conn = self._connect()

    # -- lifecycle ------------------------------------------------------------- #

    def _connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.path))
        con.execute("PRAGMA journal_mode=WAL")
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        if mode != "wal":
            raise RuntimeError(f"ledger wajib WAL, dapat {mode!r}")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        con.executescript(_SCHEMA)
        return con

    def _open_readonly_copy(self) -> sqlite3.Connection:
        """Snapshot: salinan privat + query_only. Tidak pernah menulis DB hidup."""
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="wth-ledger-snap-")) / "snapshot.sqlite"
        shutil.copy2(self.path, tmp)
        con = sqlite3.connect(str(tmp))
        con.execute("PRAGMA query_only=ON")
        return con

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
            self._conn.close()
            self._conn = None

    # -- read ------------------------------------------------------------------ #

    def count_events(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def tail_hash(self) -> str | None:
        row = self._conn.execute(
            "SELECT hash FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def current_revision(self) -> int:
        row = self._conn.execute(
            "SELECT ledger_revision FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else 0

    def events(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY sequence"
        ).fetchall()
        return [dict(r) for r in rows]

    def verify_chain(self) -> bool:
        rows = self._conn.execute(
            "SELECT sequence, previous_event_hash, hash, request_json, result_json "
            "FROM events ORDER BY sequence"
        ).fetchall()
        prev = ZERO_HASH
        for expected_seq, (sequence, prev_hash, stored_hash, request_json, result_json) in enumerate(
            rows, start=1
        ):
            # Urutan harus kontigu 1..N; celah = middle dihapus.
            if sequence != expected_seq:
                return False
            if prev_hash != prev:
                return False
            payload = self._event_payload(
                request_json=request_json, result_json=result_json
            )
            if sha256_bytes((prev + payload).encode("utf-8")) != stored_hash:
                return False
            prev = stored_hash
        return True

    def _event_payload(self, *, request_json: str | None, result_json: str | None) -> str:
        return (request_json or "") + "\n" + (result_json or "")

    # -- append ---------------------------------------------------------------- #

    def append(
        self,
        event_type: str,
        *,
        idempotency_key: str,
        object_type: str = "",
        object_id: str = "",
        actor_id: str = "",
        actor_role: str = "",
        payload: dict | None = None,
        event_id: str | None = None,
    ) -> dict:
        if self.read_only:
            raise sqlite3.OperationalError("snapshot read-only")
        canonical = _canonical(payload)
        row = self._conn.execute(
            "SELECT sequence, request_json, result_json FROM events "
            "WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is not None:
            seq, existing_req, existing_res = row
            if (existing_req or "") == canonical and (existing_res or "") == "":
                return {
                    "sequence": seq,
                    "event_id": self._event_id_for_sequence(seq),
                    "event_type": event_type,
                    "idempotency_key": idempotency_key,
                    "previous_event_hash": self._prev_hash_for_sequence(seq),
                    "hash": self._hash_for_sequence(seq),
                    "ledger_revision": self._revision_for_sequence(seq),
                }
            raise IdempotencyConflictError(
                f"idempotency_key={idempotency_key!r} dipakai ulang dengan payload berbeda"
            )

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            prev_hash = self.tail_hash() or ZERO_HASH
            sequence = self.count_events() + 1
            revision = self.current_revision() + 1
            payload_json = canonical
            digest = sha256_bytes(
                (
                    prev_hash
                    + self._event_payload(request_json=payload_json, result_json="")
                ).encode("utf-8")
            )
            self._conn.execute(
                "INSERT INTO events (sequence, event_id, event_type, object_type, "
                "object_id, actor_id, actor_role, idempotency_key, request_hash, "
                "request_json, result_json, previous_event_hash, hash, "
                "ledger_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sequence,
                    event_id or f"evt-{sequence}",
                    event_type,
                    object_type,
                    object_id,
                    actor_id,
                    actor_role,
                    idempotency_key,
                    None,
                    payload_json,
                    "",
                    prev_hash,
                    digest,
                    revision,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return {
            "sequence": sequence,
            "event_id": event_id or f"evt-{sequence}",
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "previous_event_hash": prev_hash,
            "hash": digest,
            "ledger_revision": revision,
        }

    def _event_id_for_sequence(self, seq: int) -> str:
        row = self._conn.execute(
            "SELECT event_id FROM events WHERE sequence = ?", (seq,)
        ).fetchone()
        return row[0] if row else ""

    def _prev_hash_for_sequence(self, seq: int) -> str:
        row = self._conn.execute(
            "SELECT previous_event_hash FROM events WHERE sequence = ?", (seq,)
        ).fetchone()
        return row[0] if row else ZERO_HASH

    def _hash_for_sequence(self, seq: int) -> str:
        row = self._conn.execute(
            "SELECT hash FROM events WHERE sequence = ?", (seq,)
        ).fetchone()
        return row[0] if row else ZERO_HASH

    def _revision_for_sequence(self, seq: int) -> int:
        row = self._conn.execute(
            "SELECT ledger_revision FROM events WHERE sequence = ?", (seq,)
        ).fetchone()
        return row[0] if row else 0

    def snapshot(self) -> "Ledger":
        if self.read_only:
            raise sqlite3.OperationalError("snapshot read-only")
        try:
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            pass
        return Ledger(self.path, read_only=True)
