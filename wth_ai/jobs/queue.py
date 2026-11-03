"""Local job queue layered over the JobStore (no external broker)."""

from __future__ import annotations

from wth_ai.jobs.model import Job
from wth_ai.jobs.store import JobStore


class JobQueue:
    """Priority-aware FIFO queue backed by job status in the store."""

    def __init__(self, store: JobStore) -> None:
        self.store = store

    def enqueue(self, job: Job) -> Job:
        existing = self.store.get_job(job.job_id)
        if existing is None:
            return self.store.create_job(job)
        return self.store.update_status(job.job_id, "queued") or existing

    def dequeue(self) -> Job | None:
        """Claim the next queued job atomically and return it as `running`.

        Delegates to `JobStore.claim_next_queued()`: the previous body read
        `next_queued()` and then wrote `status='running'` in a separate
        statement keyed only on `job_id`, so two workers could claim and
        execute the same job (TOCTOU).
        """
        return self.store.claim_next_queued()

    def peek(self) -> Job | None:
        return self.store.next_queued()

    def requeue(self, job_id: str) -> Job | None:
        return self.store.update_status(job_id, "queued")

    def list_queue(self) -> list[Job]:
        jobs = self.store.list_jobs(status="queued", limit=1000)
        from wth_ai.jobs.model import PRIORITY_RANK
        jobs.sort(key=lambda j: (PRIORITY_RANK.get(j.priority, 1), j.created_at))
        return jobs

    def set_priority(self, job_id: str, priority: str) -> Job | None:
        return self.store._update(job_id, {"priority": priority})

    def mark_running(self, job_id: str) -> Job | None:
        return self.store.update_status(job_id, "running")

    def mark_done(self, job_id: str) -> Job | None:
        return self.store.update_status(job_id, "completed")

    def mark_failed(self, job_id: str, error: str = "") -> Job | None:
        return self.store.fail_job(job_id, error)
