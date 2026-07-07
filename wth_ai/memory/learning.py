"""Cross-session hunt memory learning — SQLite graph."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.models import tech_stack_key


@dataclass
class LearningRecord:
    tech_stack: str
    vuln_class: str
    success: bool
    tools_used: list[str]
    payload: str = ""
    endpoint_pattern: str = ""
    session_id: str = ""


class HuntMemoryLearning:
    """SQLite store: (tech_stack, vuln_class) → success_rate, tools, payloads."""

    def __init__(self, db_path: Path | None = None) -> None:
        home = Path.home() / ".wth-ai" / "data"
        home.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path or home / "hunt_learning.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tech_stack TEXT NOT NULL,
                    vuln_class TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    tools_used TEXT,
                    payload TEXT,
                    endpoint_pattern TEXT,
                    session_id TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tech_vuln
                ON outcomes(tech_stack, vuln_class)
            """)

    def record(self, rec: LearningRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO outcomes
                   (tech_stack, vuln_class, success, tools_used, payload, endpoint_pattern, session_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.tech_stack.lower(),
                    rec.vuln_class.lower(),
                    1 if rec.success else 0,
                    json.dumps(rec.tools_used),
                    rec.payload[:500],
                    rec.endpoint_pattern[:200],
                    rec.session_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def success_rate(self, tech_stack: str | dict[str, Any], vuln_class: str) -> float:
        tech_key = tech_stack_key(tech_stack)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT AVG(success), COUNT(*) FROM outcomes
                   WHERE tech_stack = ? AND vuln_class = ?""",
                (tech_key, vuln_class.lower()),
            ).fetchone()
        if not row or not row[1]:
            return 0.0
        return round(float(row[0] or 0), 2)

    def top_patterns(self, tech_stack: str | dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
        tech_key = tech_stack_key(tech_stack)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT vuln_class, AVG(success) as rate, COUNT(*) as n,
                          GROUP_CONCAT(DISTINCT endpoint_pattern) as patterns
                   FROM outcomes WHERE tech_stack = ?
                   GROUP BY vuln_class HAVING n >= 1
                   ORDER BY rate DESC, n DESC LIMIT ?""",
                (tech_key, limit),
            ).fetchall()
        return [
            {
                "vuln_class": r[0],
                "success_rate": round(float(r[1]), 2),
                "count": r[2],
                "patterns": (r[3] or "").split(",")[:3],
            }
            for r in rows
        ]

    def build_planner_context(self, tech_stack: str | dict[str, Any]) -> str:
        tech_key = tech_stack_key(tech_stack)
        patterns = self.top_patterns(tech_key)
        if not patterns:
            return ""
        lines = [f"=== HUNT MEMORY (tech: {tech_key}) ==="]
        for p in patterns:
            pct = int(p["success_rate"] * 100)
            lines.append(
                f"- {p['vuln_class']}: {pct}% success ({p['count']} engagements)"
                + (f" — patterns: {', '.join(p['patterns'])}" if p["patterns"] else "")
            )
        lines.append("=== END HUNT MEMORY ===")
        return "\n".join(lines)

    def engagement_nudge(self, tech_stack: str | dict[str, Any]) -> str:
        """Suggest retry tactics from prior wins (for engagement loop)."""
        tech_key = tech_stack_key(tech_stack)
        patterns = self.top_patterns(tech_key, limit=3)
        if not patterns:
            return ""
        best = patterns[0]
        if best["success_rate"] < 0.5:
            return ""
        return (
            f"Learning nudge: prior {best['vuln_class']} wins on {tech_key} "
            f"({int(best['success_rate']*100)}%) — consider retry on similar endpoints."
        )

    def record_from_finding(
        self,
        tech_stack: str,
        vuln_class: str,
        *,
        verified: bool,
        tools: list[str] | None = None,
        endpoint: str = "",
        session_id: str = "",
    ) -> None:
        self.record(LearningRecord(
            tech_stack=tech_stack,
            vuln_class=vuln_class,
            success=verified,
            tools_used=tools or [],
            endpoint_pattern=endpoint,
            session_id=session_id,
        ))
