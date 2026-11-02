"""Cron ticker — enqueue jobs on schedule."""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from wth_ai.cron.store import CronEntry, CronStore
from wth_ai.jobs.manager import JobManager
from wth_ai.recon.realtime import JITTER_RATIO, parse_interval


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
        # Jitter (+/-10%) so repeats are not perfectly periodic. The jitter math
        # existed in `recon.realtime.WatchConfig.next_run_at()` but nothing ever
        # called it, so scheduled runs fired at the exact same offset every
        # interval. Seeded from (cron_id, last_run_at) so the decision is STABLE
        # across ticks within one interval -- otherwise a shared RNG would flip
        # `_due` back and forth near the boundary.
        seed = f"{entry.cron_id}:{entry.last_run_at}"
        jitter = random.Random(seed).uniform(-JITTER_RATIO, JITTER_RATIO) * interval
        return (now - last) >= timedelta(seconds=interval + jitter)

    def _enqueue(self, entry: CronEntry):
        if entry.job_type == "recon":
            return self.jobs.create_job(
                "recon",
                target=entry.target,
                profile=entry.profile,
                created_by="cron",
                # The watch entry itself was created by an explicit operator
                # `wth recon watch` command, so authorization is asserted here,
                # at that boundary -- the recon handler no longer defaults it to
                # True. `realtime` is set because `record_run()` only fires when
                # it is, and nothing ever set it: a watch's last_run_at /
                # last_job_id / runs stayed permanently stale even though the
                # scans ran.
                metadata={"authorized": True, "realtime": True, "profile": entry.profile},
            )
        return self.jobs.create_job(
            "custom",
            target=entry.target,
            metadata={"cron_id": entry.cron_id},
            created_by="cron",
        )
