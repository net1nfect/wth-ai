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
from wth_ai.models import PermissionMode
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
        permission_mode: PermissionMode | str | None = None,
        idempotency_key: str | None = None,
    ) -> Job:
        # Idempotency is checked BEFORE policy/persistence so an accidental
        # duplicate submit (CLI retry, bot redelivery, crash-resume) returns
        # the SAME job instead of spawning a second scan. A key reused for a
        # different intent fails closed rather than silently returning the
        # wrong job -- dropping a real request is worse than a duplicate.
        if idempotency_key:
            existing = self.store.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                wanted = JobStore.intent_fingerprint(type, target, profile)
                have = existing.metadata.get("intent_fingerprint") or JobStore.intent_fingerprint(
                    existing.type, existing.target, existing.profile,
                )
                if wanted == have:
                    return existing
                rejected = Job(
                    type=type, target=target, profile=profile, priority=priority,
                    created_by=created_by, metadata={**(metadata or {}), "idempotency_key": idempotency_key},
                )
                rejected.status = "failed"
                rejected.error = "idempotency: key reused with different intent"
                self.store.create_job(rejected)
                self.store.append_log(
                    rejected.job_id,
                    f"REJECTED idempotency conflict: key={idempotency_key} "
                    f"existing={existing.job_id} type={existing.type} target={existing.target}",
                )
                self._audit(
                    f"idempotency_conflict job={rejected.job_id} key={idempotency_key} "
                    f"existing={existing.job_id}"
                )
                return rejected

        allowed, reason = self.policy.evaluate(
            type=type, target=target, forbidden=forbidden, permission_mode=permission_mode,
        )
        meta = dict(metadata or {})
        if requires_approval:
            meta["requires_approval"] = True
        if idempotency_key:
            meta["idempotency_key"] = idempotency_key
            meta["intent_fingerprint"] = JobStore.intent_fingerprint(type, target, profile)
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
