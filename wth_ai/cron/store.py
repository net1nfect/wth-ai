"""Persist cron job definitions."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contextlib import contextmanager
from typing import Iterator

from wth_ai.audit import audit_log
from wth_ai.workspace import atomic_write_text, wth_home


@dataclass
class CronEntry:
    cron_id: str
    job_type: str
    target: str
    schedule: str
    profile: str = "recon-only"
    enabled: bool = True
    last_run_at: str | None = None
    next_run_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cron_id": self.cron_id,
            "job_type": self.job_type,
            "target": self.target,
            "schedule": self.schedule,
            "profile": self.profile,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CronEntry":
        return cls(
            cron_id=d["cron_id"],
            job_type=d["job_type"],
            target=d["target"],
            schedule=d["schedule"],
            profile=d.get("profile", "recon-only"),
            enabled=bool(d.get("enabled", True)),
            last_run_at=d.get("last_run_at"),
            next_run_at=d.get("next_run_at"),
            meta=dict(d.get("meta", {})),
        )


class CronStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (wth_home() / "runtime" / "cron.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # flock pada dua fd berbeda di proses yang sama akan saling menunggu,
        # jadi kedalaman kunci dilacak per-thread (reentrant).
        self._local = threading.local()

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Kunci eksklusif untuk read-modify-write `cron.json`.

        P3-3: tanpa kunci, dua ticker (TUI + gateway) sama-sama membaca
        `last_run_at` lama lalu sama-sama meng-enqueue job yang sama.
        """
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as fh:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
            except (ImportError, OSError) as exc:  # platform tanpa fcntl
                audit_log("cron_lock_unavailable", error=type(exc).__name__)
                locked = False
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0
                if locked:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _load(self) -> list[CronEntry]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [CronEntry.from_dict(x) for x in raw.get("entries", [])]

    def _save(self, entries: list[CronEntry]) -> None:
        # Tulis atomik: `write_text` yang terpotong membuat `_load` melihat file
        # rusak lalu memperlakukannya sebagai "tidak ada jadwal".
        atomic_write_text(
            self.path,
            json.dumps({"entries": [e.to_dict() for e in entries]}, indent=2) + "\n",
        )

    def list(self) -> list[CronEntry]:
        return self._load()

    def add(self, job_type: str, target: str, schedule: str, *, profile: str = "recon-only") -> CronEntry:
        with self.locked():
            return self._add(job_type, target, schedule, profile=profile)

    def _add(self, job_type: str, target: str, schedule: str, *, profile: str = "recon-only") -> CronEntry:
        entries = self._load()
        # Idempotent per (job_type, target). `wth-ai recon watch example.com`
        # previously appended a SECOND entry whenever it was re-run (e.g. to
        # change the profile), so the target was scanned twice per interval --
        # double the traffic for one watch. Re-running now updates the existing
        # entry instead of duplicating it.
        for e in entries:
            if e.job_type == job_type and e.target == target:
                e.schedule = schedule
                e.profile = profile
                e.enabled = True
                self._save(entries)
                return e
        entry = CronEntry(
            cron_id=str(uuid.uuid4())[:8],
            job_type=job_type,
            target=target,
            schedule=schedule,
            profile=profile,
        )
        entries.append(entry)
        self._save(entries)
        return entry

    def remove(self, cron_id: str) -> bool:
        with self.locked():
            return self._remove(cron_id)

    def _remove(self, cron_id: str) -> bool:
        entries = self._load()
        new = [e for e in entries if e.cron_id != cron_id]
        if len(new) == len(entries):
            return False
        self._save(new)
        return True

    def set_enabled(self, cron_id: str, enabled: bool) -> bool:
        with self.locked():
            return self._set_enabled(cron_id, enabled)

    def _set_enabled(self, cron_id: str, enabled: bool) -> bool:
        entries = self._load()
        found = False
        for e in entries:
            if e.cron_id == cron_id:
                e.enabled = enabled
                found = True
        if found:
            self._save(entries)
        return found

    def compute_next_run(self, entry: CronEntry) -> str | None:
        from wth_ai.cron.scheduler import _parse_schedule
        from datetime import datetime, timedelta, timezone

        interval = _parse_schedule(entry.schedule)
        if interval is None:
            return None
        base = datetime.now(timezone.utc)
        if entry.last_run_at:
            try:
                base = datetime.fromisoformat(entry.last_run_at)
            except ValueError:
                pass
        return (base + timedelta(seconds=interval)).isoformat()

    def refresh_next_runs(self) -> None:
        with self.locked():
            self._refresh_next_runs()

    def _refresh_next_runs(self) -> None:
        entries = self._load()
        for e in entries:
            e.next_run_at = self.compute_next_run(e)
        self._save(entries)

    def migrate_from_watch(self) -> int:
        with self.locked():
            return self._migrate_from_watch()

    def _migrate_from_watch(self) -> int:
        """Import legacy recon watch configs as cron entries."""
        from wth_ai.recon.realtime import list_watches
        migrated = 0
        existing = {(e.target, e.schedule) for e in self._load()}
        for wc in list_watches():
            sched = f"every {max(wc.interval_seconds // 3600, 1)}h"
            if (wc.target, sched) in existing:
                continue
            self.add("recon", wc.target, sched, profile=wc.profile)
            migrated += 1
        return migrated

    def update_run(self, cron_id: str) -> None:
        with self.locked():
            self._update_run(cron_id)

    def _update_run(self, cron_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        entries = self._load()
        for e in entries:
            if e.cron_id == cron_id:
                e.last_run_at = now
        self._save(entries)
