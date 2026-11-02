"""Local cron ticker for standalone CLI (no gateway required)."""

from __future__ import annotations

import threading
import time
from typing import Any

from wth_ai.cron.scheduler import CronScheduler
from wth_ai.cron.store import CronStore
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.worker import WorkerEngine


class LocalCronTicker:
    """Background thread that ticks cron and drains job queue."""

    def __init__(self, job_manager: JobManager | None = None, *, interval: float = 30.0) -> None:
        self.jobs = job_manager or JobManager()
        self.store = CronStore()
        self.scheduler = CronScheduler(self.jobs, self.store)
        self.worker = WorkerEngine(self.jobs)
        self.interval = interval
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="wth-cron-ticker")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def tick_once(self) -> list[dict[str, Any]]:
        fired = self.scheduler.tick()
        if fired:
            self.worker.run(drain=True, max_jobs=len(fired))
        return fired

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick_once()
            except Exception:
                pass
            time.sleep(self.interval)
