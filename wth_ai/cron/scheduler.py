"""Cron ticker — enqueue jobs on schedule."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from wth_ai.cron.store import CronEntry, CronStore
from wth_ai.jobs.manager import JobManager
from wth_ai.recon.realtime import parse_interval


def _parse_schedule(schedule: str) -> int | None:
    """Return interval seconds for 'every 6h' style schedules."""
    s = schedule.strip().lower()
    m = re.search(r"every\s+(\S+)", s)
    if m:
        return parse_interval(m.group(1))
    if "daily" in s:
        return 86400
    return None


class CronScheduler:
    def __init__(self, job_manager: JobManager, store: CronStore | None = None) -> None:
        self.jobs = job_manager
        self.store = store or CronStore()

    def tick(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        fired: list[dict[str, Any]] = []
        for entry in self.store.list():
            if not entry.enabled:
                continue
            if not self._due(entry, now):
                continue
            job = self._enqueue(entry)
            self.store.update_run(entry.cron_id)
            fired.append({"cron_id": entry.cron_id, "job_id": job.job_id})
        return fired

    def _due(self, entry: CronEntry, now: datetime) -> bool:
        interval = _parse_schedule(entry.schedule)
        if interval is None:
            return False
        if not entry.last_run_at:
            return True
        try:
            last = datetime.fromisoformat(entry.last_run_at)
        except ValueError:
            return True
        return (now - last) >= timedelta(seconds=interval)

    def _enqueue(self, entry: CronEntry):
        if entry.job_type == "recon":
            return self.jobs.create_job("recon", target=entry.target, profile=entry.profile, created_by="cron")
        return self.jobs.create_job(
            "custom",
            target=entry.target,
            metadata={"cron_id": entry.cron_id},
            created_by="cron",
        )
