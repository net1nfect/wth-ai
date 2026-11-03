"""SQLite-backed job store + file-backed job logs/results.

DB: ~/.wth-ai/data/jobs.sqlite
Logs: ~/.wth-ai/logs/jobs/<job_id>.log
Result mirror: ~/.wth-ai/wth-results/<job_id>/result.json
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.jobs.model import PRIORITY_RANK, Job
from wth_ai.workspace import secure_file, wth_home

_COLUMNS = (
    "job_id", "type", "status", "priority", "created_at", "updated_at",
    "started_at", "finished_at", "created_by", "target", "profile",
    "correlation_id", "progress", "result", "error", "metadata",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Persistent job records with simple queryability."""

    def __init__(self, db_path: Path | None = None, *, home: Path | None = None) -> None:
        self.home = home or wth_home()
        self.db_path = db_path or (self.home / "data" / "jobs.sqlite")
        self.logs_dir = self.home / "logs" / "jobs"
        self.results_dir = self.home / "wth-results"
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
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    created_by TEXT,
                    target TEXT,
                    profile TEXT,
                    correlation_id TEXT,
                    progress TEXT,
                    result TEXT,
                    error TEXT,
                    metadata TEXT
                )
                """
            )
        secure_file(self.db_path)

    def schema_ok(self) -> bool:
        try:
            with self._conn() as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
            return set(_COLUMNS).issubset(cols)
        except sqlite3.Error:
            return False

    # --- CRUD ---

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job.from_dict({
            **{k: row[k] for k in _COLUMNS if k not in ("progress", "result", "metadata")},
            "progress": json.loads(row["progress"]) if row["progress"] else None,
            "result": json.loads(row["result"]) if row["result"] else None,
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
        })

    def create_job(self, job: Job) -> Job:
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO jobs ({','.join(_COLUMNS)}) VALUES ({','.join('?' for _ in _COLUMNS)})",  # nosec B608
                (
                    job.job_id, job.type, job.status, job.priority, job.created_at, job.updated_at,
                    job.started_at, job.finished_at, job.created_by, job.target, job.profile,
                    job.correlation_id, json.dumps(job.progress), json.dumps(job.result),
                    job.error, json.dumps(job.metadata),
                ),
            )
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(
        self,
        *,
        status: str | None = None,
        type: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if type:
            clauses.append("type = ?")
            params.append(type)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",  # nosec B608
                (*params, limit),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def _update(self, job_id: str, fields: dict[str, Any]) -> Job | None:
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET {sets} WHERE job_id = ?",  # nosec B608
                (*fields.values(), job_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_job(job_id)

    def update_status(self, job_id: str, status: str, **extra: Any) -> Job | None:
        fields: dict[str, Any] = {"status": status}
        if status == "running" and "started_at" not in extra:
            fields["started_at"] = _now()
        if status in ("completed", "failed", "stopped", "cancelled"):
            fields["finished_at"] = _now()
        fields.update(extra)
        return self._update(job_id, fields)

    def update_progress(self, job_id: str, current: int, total: int = 100, message: str = "") -> Job | None:
        return self._update(job_id, {"progress": json.dumps({"current": current, "total": total, "message": message})})

    def set_metadata(self, job_id: str, metadata: dict[str, Any]) -> Job | None:
        return self._update(job_id, {"metadata": json.dumps(metadata)})

    def save_result(self, job_id: str, result: dict[str, Any]) -> Job | None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        rdir = self.results_dir / job_id
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return self._update(job_id, {"result": json.dumps(result)})

    def fail_job(self, job_id: str, error: str) -> Job | None:
        return self.update_status(job_id, "failed", error=error)

    def stop_job(self, job_id: str) -> Job | None:
        return self.update_status(job_id, "stopped")

    def delete_job(self, job_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        return cur.rowcount > 0

    # --- logs ---

    def log_path(self, job_id: str) -> Path:
        return self.logs_dir / f"{job_id}.log"

    def append_log(self, job_id: str, line: str) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path(job_id).open("a", encoding="utf-8") as fh:
            fh.write(f"{_now()} {line}\n")

    def read_log(self, job_id: str) -> str:
        p = self.log_path(job_id)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # --- queue helpers ---

    # Priority ordering expressed in SQL so the whole table is considered, not
    # an in-Python sort over an already-truncated window.
    _PRIORITY_ORDER_SQL = (
        "CASE priority WHEN 'high' THEN 0 WHEN 'low' THEN 2 ELSE 1 END"
    )

    def next_queued(self) -> Job | None:
        """Read-only preview of the next queued job (does NOT claim it)."""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT job_id FROM jobs WHERE status = 'queued' "  # nosec B608
                f"ORDER BY {self._PRIORITY_ORDER_SQL}, created_at ASC LIMIT 1"
            ).fetchone()
        return self.get_job(row["job_id"]) if row else None

    def claim_next_queued(self) -> Job | None:
        """Atomically claim the highest-priority, oldest queued job.

        Two defects fixed together:

        - **Atomicity.** `JobQueue.dequeue()` used to run `next_queued()`
          (a SELECT) and then a SEPARATE `update_status(...)` UPDATE keyed only
          on `job_id`, with no status predicate. Two workers sharing a store
          could both see the same `queued` row before either write landed, and
          both proceeded to execute it (verified: the same job id returned
          twice and run twice). The claim is now one IMMEDIATE transaction whose
          UPDATE carries `WHERE status = 'queued'` plus a rowcount check, so
          exactly one caller can win.

        - **FIFO leak.** `next_queued()` read `list_jobs(status="queued",
          limit=1000)` -- the 1000 NEWEST rows by `created_at DESC` -- and only
          then sorted by priority/age. With a backlog over 1000 jobs the oldest
          queued job fell outside that window and could not run until the queue
          drained below it. Ordering now happens in SQL over the whole table.
        """
        now = _now()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None => autocommit; the transaction is managed
        # explicitly below so BEGIN IMMEDIATE actually takes a write lock.
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        job_id: str | None = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT job_id FROM jobs WHERE status = 'queued' "  # nosec B608
                f"ORDER BY {self._PRIORITY_ORDER_SQL}, created_at ASC LIMIT 1"
            ).fetchone()
            if row is not None:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'running', "
                    "started_at = COALESCE(started_at, ?), updated_at = ? "
                    "WHERE job_id = ? AND status = 'queued'",
                    (now, now, row["job_id"]),
                )
                if cur.rowcount == 1:
                    job_id = row["job_id"]
            conn.execute("COMMIT")
        except sqlite3.Error:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            return None
        finally:
            conn.close()
        return self.get_job(job_id) if job_id else None
