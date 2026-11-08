"""Antrean job berbasis file untuk worker lokal (izin berbatas §1.2 poin 6).

Kenapa file + `flock`, bukan server: batas yang diizinkan hanya beberapa proses
di satu host. Antrean yang bisa dijangkau jaringan adalah fitur lain dengan
konsekuensi lain (scheduler remote), dan itu tidak dibangun di sini.

Klaim job dilakukan di dalam kunci, jadi dua worker tidak bisa memegang job yang
sama. Kunci bersifat reentrant per thread karena `claim()` memanggil helper yang
juga mengunci; `flock` pada fd kedua di proses yang sama akan mengunci diri
sendiri.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from wth_ai.workspace import atomic_write_text

try:  # pragma: no cover - platform tanpa fcntl tetap bisa jalan, tanpa antar-proses
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

class LocalQueue:
    """Antrean job satu host: `queue.json` + `queue.json.lock`."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = Path(f"{self.path}.lock")
        self._local = threading.local()

    # -- kunci ---------------------------------------------------------

    @contextmanager
    def locked(self) -> Iterator[None]:
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as handle:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                self._local.depth = 1
                yield
            finally:
                self._local.depth = 0
                if fcntl is not None:
                    with suppress(OSError):
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # -- penyimpanan ---------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {"jobs": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"jobs": {}}
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
            return {"jobs": {}}
        return data

    def _save(self, state: dict[str, Any]) -> None:
        atomic_write_text(self.path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    # -- API -----------------------------------------------------------

    def enqueue(self, payload: dict[str, Any]) -> str:
        with self.locked():
            state = self._load()
            job_id = uuid4().hex[:12]
            state["jobs"][job_id] = {
                "id": job_id,
                "payload": payload,
                "state": PENDING,
                "worker": "",
                "error": "",
            }
            self._save(state)
            return job_id

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        with self.locked():
            state = self._load()
            for job in state["jobs"].values():
                if job.get("state") == PENDING:
                    job["state"] = RUNNING
                    job["worker"] = worker_id
                    self._save(state)
                    return dict(job)
            return None

    def complete(self, job_id: str) -> None:
        with self.locked():
            state = self._load()
            job = state["jobs"].get(job_id)
            if job is not None:
                job["state"] = DONE
                job["error"] = ""
                self._save(state)

    def fail(self, job_id: str, error: str) -> None:
        with self.locked():
            state = self._load()
            job = state["jobs"].get(job_id)
            if job is not None:
                job["state"] = FAILED
                job["error"] = str(error)
                self._save(state)

    def result(self, job_id: str) -> dict[str, Any]:
        return dict(self._load()["jobs"].get(job_id) or {})

    def pending(self) -> list[dict[str, Any]]:
        jobs = self._load()["jobs"]
        return [dict(job) for job in jobs.values() if job.get("state") == PENDING]
