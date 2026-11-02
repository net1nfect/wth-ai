"""Job Manager — admission (policy), persistence, queue, and lifecycle events."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.approvals import ApprovalRegistry
from wth_ai.config import WTHConfig
from wth_ai.events import EventBus, get_event_bus
from wth_ai.jobs.model import TERMINAL_STATUSES, Job
from wth_ai.jobs.policy import PolicyEngine
from wth_ai.jobs.queue import JobQueue
from wth_ai.jobs.store import JobStore
from wth_ai.workspace import wth_home


class PolicyError(RuntimeError):
    """Raised when a job is rejected by the policy engine."""


class JobManager:
    """Single entry point for creating and managing jobs."""

    def __init__(
        self,
        config: WTHConfig | None = None,
        *,
        store: JobStore | None = None,
        queue: JobQueue | None = None,
        bus: EventBus | None = None,
        approvals: ApprovalRegistry | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.config = config or WTHConfig.load()
        self.store = store or JobStore()
        self.queue = queue or JobQueue(self.store)
        self.bus = bus or get_event_bus()
        self.approvals = approvals or ApprovalRegistry()
        self.policy = policy or PolicyEngine(self.config)
        self.audit_path = wth_home() / "logs" / "audit.log"

    def _audit(self, message: str) -> None:
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat()
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{ts} {message}\n")
        except OSError:
            pass

    # --- creation with policy ---

    def create_job(
        self,
        type: str,
        *,
        target: str | None = None,
        profile: str | None = None,
        priority: str = "normal",
        created_by: str = "cli",
        requires_approval: bool = False,
        forbidden: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> Job:
        allowed, reason = self.policy.evaluate(type=type, target=target, forbidden=forbidden)
        meta = dict(metadata or {})
        if requires_approval:
            meta["requires_approval"] = True
        job = Job(
            type=type, target=target, profile=profile, priority=priority,
            created_by=created_by, metadata=meta,
        )
        if not allowed:
            job.status = "failed"
            job.error = f"policy: {reason}"
            self.store.create_job(job)
            self.store.append_log(job.job_id, f"REJECTED by policy: {reason}")
            self._audit(f"policy_reject job={job.job_id} type={type} target={target} reason={reason}")
            self.bus.emit("app.error", source="system", job_id=job.job_id,
                          data={"reason": reason, "stage": "policy"})
            return job
        self.store.create_job(job)
        self.store.append_log(job.job_id, f"created type={type} target={target} priority={priority}")
        self.bus.emit("job.created", source=created_by, job_id=job.job_id,
                      data={"type": type, "target": target})
        return job

    # --- queries ---

    def get_job(self, job_id: str) -> Job | None:
        return self.store.get_job(job_id)

    def list_jobs(self, **kwargs: Any) -> list[Job]:
        return self.store.list_jobs(**kwargs)

    def logs(self, job_id: str) -> str:
        return self.store.read_log(job_id)

    # --- lifecycle ---

    def stop_job(self, job_id: str) -> Job | None:
        job = self.store.get_job(job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return job
        updated = self.store.stop_job(job_id)
        self.store.append_log(job_id, "stopped by request")
        self.bus.emit("job.stopped", job_id=job_id, source="system")
        return updated

    def retry_job(self, job_id: str) -> Job | None:
        job = self.store.get_job(job_id)
        if job is None or job.status not in ("failed", "stopped", "cancelled"):
            return job
        self.store._update(job_id, {"error": None})
        updated = self.queue.requeue(job_id)
        self.store.append_log(job_id, "retried -> queued")
        self.bus.emit("job.created", job_id=job_id, source="system", data={"retry": True})
        return updated

    def delete_job(self, job_id: str) -> bool:
        return self.store.delete_job(job_id)

    def summary(self) -> dict[str, Any]:
        jobs = self.store.list_jobs(limit=1000)
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.status] = counts.get(j.status, 0) + 1
        return {"total": len(jobs), "by_status": counts, "queued": counts.get("queued", 0)}
