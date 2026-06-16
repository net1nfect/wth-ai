"""Transactional SQLite result and learning data persistence."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .exceptions import PersistenceError
from .models import utc_now


class ResultsDatabase:
    """Store and retrieve engagement, finding, exploitation, and learning data."""

    TABLES = (
        "engagements",
        "findings",
        "exploitations",
        "credentials",
        "data_extractions",
        "attack_chains",
        "learning_data",
        "agent_metrics",
        "threat_intelligence",
        "audit_trail",
    )

    def __init__(self, url: str = "sqlite:///data/h1t4m.db") -> None:
        if not url.startswith("sqlite:///"):
            raise PersistenceError("This runtime supports SQLite URLs; use an adapter for PostgreSQL/MongoDB")
        self.path = Path(url.removeprefix("sqlite:///"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._create_schema()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Provide a committed or rolled-back transaction."""
        with self._lock:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(str(exc)) from exc
            finally:
                connection.close()

    def _create_schema(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS engagements "
                "(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)"
            )
            for table in self.TABLES[1:]:
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} "
                    "(id TEXT PRIMARY KEY, engagement_id TEXT, category TEXT, created_at TEXT NOT NULL, data TEXT NOT NULL)"
                )
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_engagement ON {table}(engagement_id)"
                )

    def _insert(
        self, table: str, data: dict[str, Any], engagement_id: str | None = None, category: str | None = None
    ) -> str:
        record_id = str(uuid4())
        with self.transaction() as connection:
            if table == "engagements":
                connection.execute(
                    "INSERT INTO engagements VALUES (?, ?, ?)",
                    (record_id, utc_now(), json.dumps(data, default=str)),
                )
            else:
                connection.execute(
                    f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?)",
                    (record_id, engagement_id, category, utc_now(), json.dumps(data, default=str)),
                )
            if table != "audit_trail":
                audit_id = str(uuid4())
                connection.execute(
                    "INSERT INTO audit_trail VALUES (?, ?, ?, ?, ?)",
                    (audit_id, engagement_id, table, utc_now(), json.dumps({"record_id": record_id})),
                )
        return record_id

    def create_engagement(self, engagement_data: dict[str, Any]) -> str:
        """Create an engagement record."""
        return self._insert("engagements", engagement_data)

    def store_finding(self, engagement_id: str, finding_data: dict[str, Any]) -> str:
        """Store a finding."""
        return self._insert("findings", finding_data, engagement_id, finding_data.get("type"))

    def store_exploitation(self, engagement_id: str, exploit_data: dict[str, Any]) -> str:
        """Store a controlled validation attempt."""
        return self._insert("exploitations", exploit_data, engagement_id, exploit_data.get("vuln_type"))

    def store_credential(self, engagement_id: str, cred_data: dict[str, Any]) -> str:
        """Store only redacted credential metadata."""
        safe = {**cred_data, "value": "***"} if "value" in cred_data else cred_data
        return self._insert("credentials", safe, engagement_id, safe.get("type"))

    def store_learning_data(self, engagement_id: str, category: str, data: dict[str, Any]) -> str:
        """Store a learning sample."""
        return self._insert("learning_data", data, engagement_id, category)

    def _query(self, table: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        clauses: list[str] = []
        values: list[Any] = []
        for key in ("engagement_id", "category"):
            if key in filters:
                clauses.append(f"{key} = ?")
                values.append(filters[key])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.transaction() as connection:
            rows = connection.execute(f"SELECT * FROM {table}{where}", values).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def query_findings(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Query findings by supported indexed filters."""
        return self._query("findings", filters)

    def query_successful_exploitations(self, vuln_type: str) -> list[dict[str, Any]]:
        """Query successful controlled validations by type."""
        return [
            row for row in self._query("exploitations", {"category": vuln_type})
            if row["data"].get("success")
        ]

    def get_learning_data(self, category: str) -> list[dict[str, Any]]:
        """Return learning samples by category."""
        return self._query("learning_data", {"category": category})

    def update_agent_metrics(self, agent_id: str, metrics: dict[str, Any]) -> None:
        """Persist an agent metrics snapshot."""
        self._insert("agent_metrics", {"agent_id": agent_id, **metrics}, category=agent_id)

    def export_findings(self, format: str = "json") -> str:
        """Export findings as JSON or CSV."""
        findings = self.query_findings()
        if format == "json":
            return json.dumps(findings, indent=2, default=str)
        if format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=["id", "engagement_id", "category", "created_at", "data"])
            writer.writeheader()
            writer.writerows({**row, "data": json.dumps(row["data"])} for row in findings)
            return output.getvalue()
        raise ValueError("Supported export formats are json and csv; PDF is generated by reporting")

    def backup(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup."""
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(self.path) as source, sqlite3.connect(destination_path) as target:
            source.backup(target)
        return destination_path

    def cleanup_old_data(self, days_threshold: int) -> None:
        """Delete non-engagement records older than a threshold."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_threshold)).isoformat()
        with self.transaction() as connection:
            for table in self.TABLES[1:]:
                connection.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
