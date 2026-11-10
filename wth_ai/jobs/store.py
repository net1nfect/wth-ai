"""SQLite-backed job store + file-backed job logs/results.

DB: ~/.wth-ai/data/jobs.sqlite
Logs: ~/.wth-ai/logs/jobs/<job_id>.log
Result mirror: ~/.wth-ai/wth-ai-results/<job_id>/result.json
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.jobs.model import PRIORITY_RANK, Job
from wth_ai.workspace import RESULTS_DIR_NAME, secure_file, wth_home

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
        self.results_dir = self.home / RESULTS_DIR_NAME
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
                f"INSERT INTO jobs ({','.join(_COLUMNS)}) VALUES ({','.join('?' for _ in _COLUMNS)})",
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
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def _update(self, job_id: str, fields: dict[str, Any]) -> Job | None:
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET {sets} WHERE job_id = ?",
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

    #: Urutan antrean di SQL, diturunkan dari PRIORITY_RANK. Kalau model berubah,
    #: `_priority_case()` dan test_jobs_starvation ikut menyesuaikan.
    _PRIORITY_ORDER_SQL = (
        "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
        "created_at ASC"
    )

    def claim_next(self) -> Job | None:
        """Klaim job queued berikutnya secara atomik.

        `BEGIN IMMEDIATE` mengambil write-lock SEBELUM SELECT, jadi dua worker
        tidak bisa sama-sama membaca baris queued yang sama: yang kedua menunggu
        sampai yang pertama commit, lalu melihat status sudah 'running'.

        `UPDATE ... RETURNING` sengaja tidak dipakai. Sintaks itu butuh SQLite
        >= 3.35, sedangkan repo mendukung Python >= 3.10 yang bisa terpasang di
        distro dengan SQLite lebih lama; transaksi eksplisit portabel dan
        memberi jaminan yang sama.
        """
        stamp = _now()
        conn = self._conn()
        # Autocommit: transaksi dikelola manual supaya BEGIN IMMEDIATE tidak
        # bertabrakan dengan BEGIN implisit milik modul sqlite3.
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT job_id FROM jobs WHERE status = 'queued' "
                f"ORDER BY {self._PRIORITY_ORDER_SQL} LIMIT 1"
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            job_id = row["job_id"]
            cur = conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, updated_at = ? "
                "WHERE job_id = ? AND status = 'queued'",
                (stamp, stamp, job_id),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return None
            claimed = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self._row_to_job(claimed) if claimed else None

    def next_queued(self) -> Job | None:
        """Job queued berikutnya tanpa mengubah status (read-only).

        Urutan dilakukan di SQL: memuat 1000 baris terbaru lalu menyortir di
        Python menyembunyikan job lama saat antrean lebih panjang dari jendela.
        """
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE status = 'queued' "
                f"ORDER BY {self._PRIORITY_ORDER_SQL} LIMIT 1"
            ).fetchone()
        return self._row_to_job(row) if row else None

