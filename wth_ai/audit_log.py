"""Durable SQLite-backed audit log (WU-105, PRD-006/PRD-011, ADR-008).

`AuditLogSink` is an `EventBus` subscriber (callable) -- subscribing it to
`get_event_bus()` durably persists EVERY event delivered to that bus. This
captures, for free, everything already emitted today (job lifecycle, recon
events, `app.error`, and -- as of this WU -- `invalid_state_transition`,
see `events.py::EVENT_TYPES`) without adding audit-specific call sites
throughout the codebase.

Scope decision: this is ONE of several pre-existing, independent audit
mechanisms (`jobs/manager.py::_audit()` plain-text log, `skills/hub.py`,
`mcp/manager.py::_audit()` JSONL) -- none of those are touched/replaced
here (each is scoped to its own subsystem and works standalone). This sink
is additive: a durable, queryable, EventBus-wide record, not a replacement.

`tainted` column: present in the schema (forward-compatible) but currently
ALWAYS NULL -- `context.tainted` (WU-007 remainder, prompt-injection taint
tracking) doesn't exist as a real, populated field anywhere yet. Nothing
sets `event.data["tainted"]` today; this column has no live data until that
infrastructure lands.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from wth_ai.events import Event
from wth_ai.workspace import secure_file, wth_home

_REQUIRED_COLUMNS = {
    "id", "type", "timestamp", "source", "severity", "job_id", "correlation_id", "tainted", "data",
}


class AuditLogSink:
    """Callable -- pass the instance directly to `EventBus.subscribe()`."""

    def __init__(self, db_path: Path | None = None, *, home: Path | None = None) -> None:
        self.home = home or wth_home()
        self.db_path = db_path or (self.home / "data" / "audit_log.sqlite")
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
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    source TEXT,
                    severity TEXT,
                    job_id TEXT,
                    correlation_id TEXT,
                    tainted INTEGER,
                    data TEXT
                )
                """
            )
        secure_file(self.db_path)

    def schema_ok(self) -> bool:
        try:
            with self._conn() as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}
            return _REQUIRED_COLUMNS.issubset(cols)
        except sqlite3.Error:
            return False

    def __call__(self, event: Event) -> None:
        """`EventBus.publish()` calls `handler(event)` -- matches that
        Subscriber signature directly so an `AuditLogSink` instance can be
        passed straight to `bus.subscribe(sink)`."""
        self.record(event)

    def record(self, event: Event) -> None:
        tainted = event.data.get("tainted") if isinstance(event.data, dict) else None
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_log
                    (id, type, timestamp, source, severity, job_id, correlation_id, tainted, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id, event.type, event.timestamp, event.source, event.severity,
                    event.job_id, event.correlation_id,
                    int(bool(tainted)) if tainted is not None else None,
                    json.dumps(event.data),
                ),
            )

    def query(self, *, event_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_log"
        params: tuple[Any, ...] = ()
        if event_type:
            sql += " WHERE type = ?"
            params = (event_type,)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params = params + (limit,)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
