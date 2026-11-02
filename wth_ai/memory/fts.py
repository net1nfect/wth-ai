"""SQLite FTS5 session memory index."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.workspace import wth_home


class SessionFtsIndex:
    """Full-text search across slash I/O and supervisor turns."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (wth_home() / "data" / "session_fts.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
                    session_key, role, content, created_at, tokenize='porter'
                )
            """)

    def index_turn(self, session_key: str, user_text: str, assistant_text: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO session_fts(session_key, role, content, created_at) VALUES (?,?,?,?)",
                (session_key, "user", user_text[:4000], ts),
            )
            conn.execute(
                "INSERT INTO session_fts(session_key, role, content, created_at) VALUES (?,?,?,?)",
                (session_key, "assistant", assistant_text[:4000], ts),
            )

    def index_snippet(self, session_key: str, role: str, content: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO session_fts(session_key, role, content, created_at) VALUES (?,?,?,?)",
                (session_key, role, content[:4000], ts),
            )

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        q = query.strip()
        if not q:
            return []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT session_key, role, snippet(session_fts, 2, '«', '»', '…', 32) AS snip, created_at
                   FROM session_fts WHERE session_fts MATCH ? ORDER BY rank LIMIT ?""",
                (q, limit),
            ).fetchall()
        return [
            {"session_key": r[0], "role": r[1], "snippet": r[2], "created_at": r[3]}
            for r in rows
        ]
