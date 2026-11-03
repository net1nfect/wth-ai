"""SQLite-backed `EngagementStore` (WU-101, PRD-003, ADR-011).

`fetch()` ALWAYS reads fresh from the database -- no caching, no
memoization, nothing held in memory between calls. This is the property
`ExecutionBroker`'s future fetch-fresh enforcement (WU-104, ADR-011's
"lapis 2" -- independent runtime check, not just the property-guard in
`Engagement` itself) depends on structurally, the same lesson learned from
`LoopState` being provably vestigial (WU-006).

Scope decision: this WU is the store ONLY -- CRUD + fetch-fresh semantics.
Wiring `ExecutionBroker.execute()` to actually call `fetch()` before every
execution (the "integrasi broker" half of WU-101's original name) is
deferred to WU-104, consistent with WU-102's own scope decision (broker
skeleton doesn't consume `Engagement` yet -- no `EngagementStore` existed
until this WU). `/engagement create|authorize|pause|resume|abort` CLI
commands (PRD-003 FR#2) are also NOT built here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from wth_ai.engagement.model import Engagement
from wth_ai.workspace import secure_file, wth_home

_REQUIRED_COLUMNS = {"id", "name", "status", "created_by", "created_at", "updated_at", "data"}


class EngagementStore:
    """Persistent `Engagement` records, queryable by status."""

    def __init__(self, db_path: Path | None = None, *, home: Path | None = None) -> None:
        self.home = home or wth_home()
        self.db_path = db_path or (self.home / "data" / "engagements.sqlite")
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS engagements (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )
        secure_file(self.db_path)

    def schema_ok(self) -> bool:
        try:
            with self._conn() as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(engagements)")}
            return _REQUIRED_COLUMNS.issubset(cols)
        except sqlite3.Error:
            return False

    # --- CRUD ---

    def save(self, engagement: Engagement) -> Engagement:
        """Upsert. Caller calls this explicitly after construction AND after
        every successful `.transition()` -- `Engagement` itself has zero
        knowledge of how/whether it's persisted (clean separation, no
        auto-persist-on-mutate magic)."""
        data = engagement.to_dict()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO engagements (id, name, status, created_by, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    data = excluded.data
                """,
                (
                    engagement.id, engagement.name, engagement.status.value, engagement.created_by,
                    engagement.created_at, engagement.updated_at, json.dumps(data),
                ),
            )
        return engagement

    def fetch(self, engagement_id: str) -> Engagement | None:
        """ALWAYS fresh -- see module docstring. `Engagement.status` on the
        returned object still auto-computes EXPIRED live even if the stored
        `status` column is stale (nothing re-saved since expiry) -- the
        `status` column is a query-filter convenience, not authoritative."""
        with self._conn() as conn:
            row = conn.execute("SELECT data FROM engagements WHERE id = ?", (engagement_id,)).fetchone()
        if not row:
            return None
        return Engagement.from_dict(json.loads(row["data"]))

    def list_engagements(self, *, status: str | None = None) -> list[Engagement]:
        query = "SELECT data FROM engagements"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [Engagement.from_dict(json.loads(r["data"])) for r in rows]

    def delete(self, engagement_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM engagements WHERE id = ?", (engagement_id,))
