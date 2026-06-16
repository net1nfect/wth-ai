"""Worker Engine — runs queued jobs, emits lifecycle events, honors approvals."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.approvals import STATUS_APPROVED, STATUS_DENIED, STATUS_EXPIRED
from wth_ai.jobs.handlers import Handler, default_handlers
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.model import Job
from wth_ai.workspace import wth_home


class JobContext:
    """Handler-facing context: progress, logging, cooperative stop."""

    def __init__(self, manager: JobManager, job: Job) -> None:
        self._manager = manager
        self._job = job
        self.job_id = job.job_id

    def log(self, line: str) -> None:
        self._manager.store.append_log(self.job_id, line)

    def progress(self, current: int, total: int = 100, message: str = "") -> None:
        self._manager.store.update_progress(self.job_id, current, total, message)
        self._manager.bus.emit(
            "job.progress", job_id=self.job_id,
            data={"current": current, "total": total, "message": message},
        )

    def should_stop(self) -> bool:
        fresh = self._manager.store.get_job(self.job_id)
        return bool(fresh and fresh.status in ("stopped", "cancelled"))


class WorkerEngine:
    def __init__(
        self,
        manager: JobManager,
        *,
        handlers: dict[str, Handler] | None = None,
        timeout: int = 120,
    ) -> None:
        self.manager = manager
        self.store = manager.store
        self.queue = manager.queue
        self.bus = manager.bus
        self.approvals = manager.approvals
        self.handlers = handlers or default_handlers()
        self.timeout = timeout
        self._running = False
        self.status_path = wth_home() / "runtime" / "worker.status"

    def register(self, job_type: str, handler: Handler) -> None:
        self.handlers[job_type] = handler

    # --- status persistence ---

    def _write_status(self, running: bool) -> None:
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(json.dumps({
                "running": running,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
        except OSError:
            pass

    def status(self) -> dict[str, Any]:
        base = {"running": False, "updated_at": None}
        if self.status_path.exists():
            try:
                base.update(json.loads(self.status_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        base["summary"] = self.manager.summary()
        return base

    def stop(self) -> None:
        self._running = False
        self._write_status(False)

    # --- approval resume ---

    def _resume_waiting(self) -> None:
        for job in self.store.list_jobs(status="waiting_approval", limit=1000):
            approval_id = job.metadata.get("approval_id")
            if not approval_id:
                continue
            rec = self.approvals.get(approval_id)
            status = rec["status"] if rec else None
            if status == STATUS_APPROVED:
                self.queue.requeue(job.job_id)
                self.store.append_log(job.job_id, f"approval {approval_id} approved -> resumed")
                self.bus.emit("job.approved", job_id=job.job_id, data={"approval_id": approval_id})
            elif status in (STATUS_DENIED, STATUS_EXPIRED):
                self.store.fail_job(job.job_id, f"approval {status}")
                self.store.append_log(job.job_id, f"approval {approval_id} {status} -> failed")
                self.bus.emit("job.denied", job_id=job.job_id, data={"approval_id": approval_id})
                self.bus.emit("job.failed", job_id=job.job_id, data={"reason": f"approval {status}"})

    # --- single job ---

    def run_once(self) -> dict[str, Any]:
        self._resume_waiting()
        job = self.queue.peek()
        if job is None:
            return {"status": "idle"}

        # Approval gate: first encounter creates the request and parks the job.
        if job.metadata.get("requires_approval") and not job.metadata.get("approval_id"):
            ap = self.approvals.create(
                job_id=job.job_id, action=f"{job.type} {job.target or ''}".strip(), risk="medium",
                reason="job requires approval",
            )
            meta = {**job.metadata, "approval_id": ap["approval_id"]}
            self.store.set_metadata(job.job_id, meta)
            self.store.update_status(job.job_id, "waiting_approval")
            self.store.append_log(job.job_id, f"waiting approval {ap['approval_id']}")
            self.bus.emit("job.approval_required", job_id=job.job_id, data={"approval_id": ap["approval_id"]})
            return {"status": "waiting_approval", "job_id": job.job_id, "approval_id": ap["approval_id"]}

        running = self.queue.dequeue()
        if running is None:
            return {"status": "idle"}
        return self._run_job(running)

    def _run_job(self, job: Job) -> dict[str, Any]:
        self.store.append_log(job.job_id, "started")
        self.bus.emit("job.started", job_id=job.job_id, data={"type": job.type})
        handler = self.handlers.get(job.type)
        if handler is None:
            self.store.fail_job(job.job_id, f"no handler for type '{job.type}'")
            self.bus.emit("job.failed", job_id=job.job_id, data={"reason": "no_handler"})
            return {"status": "failed", "job_id": job.job_id, "error": "no_handler"}

        ctx = JobContext(self.manager, job)
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                result = ex.submit(handler, job, ctx).result(timeout=self.timeout)
        except FutureTimeout:
            self.store.fail_job(job.job_id, "timeout")
            self.store.append_log(job.job_id, "timeout")
            self.bus.emit("job.failed", job_id=job.job_id, data={"reason": "timeout"})
            return {"status": "failed", "job_id": job.job_id, "error": "timeout"}
        except Exception as exc:  # noqa: BLE001 - capture handler failure
            self.store.fail_job(job.job_id, str(exc))
            self.store.append_log(job.job_id, f"exception: {exc}")
            self.bus.emit("job.failed", job_id=job.job_id, data={"reason": str(exc)})
            return {"status": "failed", "job_id": job.job_id, "error": str(exc)}

        fresh = self.store.get_job(job.job_id)
        if fresh and fresh.status in ("stopped", "cancelled"):
            self.bus.emit("job.stopped", job_id=job.job_id)
            return {"status": fresh.status, "job_id": job.job_id}

        result = result if isinstance(result, dict) else {"value": result}
        self.store.save_result(job.job_id, result)
        self.store.update_status(job.job_id, "completed")
        self.store.append_log(job.job_id, "completed")
        self.bus.emit("job.completed", job_id=job.job_id, data={"result_keys": list(result.keys())})
        return {"status": "completed", "job_id": job.job_id, "result": result}

    # --- loop ---

    def run(
        self,
        *,
        drain: bool = True,
        max_jobs: int | None = None,
        idle_sleep: float = 1.0,
    ) -> dict[str, Any]:
        """Process jobs. In drain mode (default) stop once nothing is runnable;
        otherwise loop as a daemon until ``stop()``.
        """
        self._running = True
        self._write_status(True)
        processed = 0
        try:
            while self._running:
                res = self.run_once()
                if res["status"] in ("idle", "waiting_approval"):
                    if drain:
                        break
                    time.sleep(idle_sleep)
                    continue
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    break
        finally:
            self._running = False
            self._write_status(False)
        return {"processed": processed}
