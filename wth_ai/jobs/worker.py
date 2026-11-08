"""Worker Engine — runs queued jobs, emits lifecycle events, honors approvals."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any

from wth_ai.approvals import STATUS_APPROVED, STATUS_DENIED, STATUS_EXPIRED
from wth_ai.jobs.handlers import Handler, default_handlers
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.model import Job
from wth_ai.workspace import wth_home


# Wall-clock budget per job type, in seconds. Overridable via the `timeouts`
# constructor argument; unknown types fall back to `timeout`.
#
# The single flat default of 120s could not accommodate the work these handlers
# actually do: a `recon` job with the default profile plans up to 40 tools and
# `ReconEngine.tool_timeout` allows 60s *per tool*, so a legitimate scan could
# run for many minutes. It was marked `failed (timeout)` while its handler
# thread kept running and writing artifacts into wth-ai-results/<job_id>/ for a
# job already recorded as failed.
DEFAULT_JOB_TIMEOUTS: dict[str, int] = {
    "recon": 3600,
    "executor": 1800,
    "report": 900,
    # Autopilot menjalankan beberapa langkah HTTP berbatas per cycle; batas waktu
    # eksplisit supaya job tidak pernah ditandai gagal sementara langkahnya jalan.
    "autopilot": 1800,
}
DEFAULT_TIMEOUT = 120


class JobContext:
    """Handler-facing context: progress, logging, cooperative stop."""

    def __init__(self, manager: JobManager, job: Job) -> None:
        self._manager = manager
        self._job = job
        self.job_id = job.job_id

    def log(self, line: str) -> None:
        self._manager.store.append_log(self.job_id, line)
        self._manager.bus.emit(
            "job.log", job_id=self.job_id, data={"line": line},
        )

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
        timeout: int = DEFAULT_TIMEOUT,
        timeouts: dict[str, int] | None = None,
    ) -> None:
        self.manager = manager
        self.store = manager.store
        self.queue = manager.queue
        self.bus = manager.bus
        self.approvals = manager.approvals
        self.handlers = handlers or default_handlers()
        self.timeout = timeout
        # Per-job-type budget; `timeout` remains the fallback for unknown types
        # and the value tests override to exercise the timeout path quickly.
        self.timeouts = dict(DEFAULT_JOB_TIMEOUTS)
        if timeouts:
            self.timeouts.update(timeouts)
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
            elif status is None:
                # The record is gone (approvals.json deleted/corrupted/reset).
                # Previously this fell through with no action at all, leaving the
                # job parked forever: it is not `queued` so run_once() never
                # picks it up again, and retry_job() refuses any status outside
                # failed/stopped/cancelled. Failing it closed makes the job
                # recoverable -- a subsequent retry goes back through the
                # approval gate and requests a fresh approval.
                self.store.fail_job(job.job_id, f"approval {approval_id} record missing")
                self.store.append_log(
                    job.job_id, f"approval {approval_id} record missing -> failed (retry to re-request)"
                )
                self.bus.emit("job.failed", job_id=job.job_id, data={"reason": "approval record missing"})

    # --- single job ---

    def run_once(self) -> dict[str, Any]:
        self._resume_waiting()

        # Claim BEFORE inspecting: the claim is what makes this race-free.
        # Previously the worker peeked (SELECT) and only later dequeued, so two
        # workers could both act on the same queued job.
        job = self.queue.dequeue()
        if job is None:
            return {"status": "idle"}

        # Approval gate on the CLAIMED job. STATUS-based, not presence-based:
        # the old check was `requires_approval and not metadata.get("approval_id")`
        # -- presence of an id, not whether it was approved. `_resume_waiting()`
        # fails a job on DENY but never clears `approval_id`, so `wth-ai job retry`
        # on a denied job (retry accepts status failed/stopped/cancelled) put it
        # back in the queue with the id still present, the gate was skipped and
        # the job executed with NO approval at all. Now the stored record itself
        # must be APPROVED; a resolved non-approved record fails closed, and a
        # missing record (e.g. approvals.json lost) gets a fresh request instead
        # of leaving the job parked forever.
        if job.metadata.get("requires_approval"):
            approval_id = job.metadata.get("approval_id")
            rec = self.approvals.get(approval_id) if approval_id else None
            status = rec.get("status") if rec else None

            if status in (STATUS_DENIED, STATUS_EXPIRED):
                self.store.fail_job(job.job_id, f"approval {status}")
                self.store.append_log(job.job_id, f"approval {approval_id} {status} -> failed")
                self.bus.emit("job.denied", job_id=job.job_id, data={"approval_id": approval_id})
                self.bus.emit("job.failed", job_id=job.job_id, data={"reason": f"approval {status}"})
                return {"status": "failed", "job_id": job.job_id, "error": f"approval {status}"}

            if status != STATUS_APPROVED:
                ap = rec or self.approvals.create(
                    job_id=job.job_id, action=f"{job.type} {job.target or ''}".strip(), risk="medium",
                    reason="job requires approval",
                )
                meta = {**job.metadata, "approval_id": ap["approval_id"]}
                self.store.set_metadata(job.job_id, meta)
                self.store.update_status(job.job_id, "waiting_approval")
                self.store.append_log(job.job_id, f"waiting approval {ap['approval_id']}")
                self.bus.emit("job.approval_required", job_id=job.job_id, data={"approval_id": ap["approval_id"]})
                return {"status": "waiting_approval", "job_id": job.job_id, "approval_id": ap["approval_id"]}

        return self._run_job(job)

    def _run_job(self, job: Job) -> dict[str, Any]:
        self.store.append_log(job.job_id, "started")
        self.bus.emit("job.started", job_id=job.job_id, data={"type": job.type})
        handler = self.handlers.get(job.type)
        if handler is None:
            self.store.fail_job(job.job_id, f"no handler for type '{job.type}'")
            self.bus.emit("job.failed", job_id=job.job_id, data={"reason": "no_handler"})
            return {"status": "failed", "job_id": job.job_id, "error": "no_handler"}

        ctx = JobContext(self.manager, job)
        # WU-108: TIDAK pakai `with ThreadPoolExecutor(...) as ex:` -- __exit__
        # memanggil shutdown(wait=True) TANPA SYARAT, jadi saat .result()
        # timeout, keluar dari blok `with` tetap BLOK sampai thread handler
        # yang sudah dianggap timeout benar-benar selesai sendiri (berapa
        # lama pun) -- "timeout kosmetik": job di-mark failed, tapi
        # WorkerEngine tidak benar-benar bisa lanjut ke job berikutnya.
        # shutdown(wait=False) di jalur except membiarkan pemanggil lanjut
        # segera; thread yang sudah telanjur berjalan tetap orphan di
        # background (Python tidak bisa kill thread paksa) -- membunuh
        # PROSES nyata yang mungkin dijalankan handler butuh
        # RunningActionRegistry (WU-106), di luar scope WU ini.
        ex = ThreadPoolExecutor(max_workers=1)
        budget = self.timeouts.get(job.type, self.timeout)
        try:
            result = ex.submit(handler, job, ctx).result(timeout=budget)
        except FutureTimeout:
            ex.shutdown(wait=False)
            self.store.fail_job(job.job_id, "timeout")
            self.store.append_log(job.job_id, f"timeout after {budget}s")
            self.bus.emit("job.failed", job_id=job.job_id, data={"reason": "timeout"})
            return {"status": "failed", "job_id": job.job_id, "error": "timeout"}
        except Exception as exc:  # noqa: BLE001 - capture handler failure
            ex.shutdown(wait=False)
            self.store.fail_job(job.job_id, str(exc))
            self.store.append_log(job.job_id, f"exception: {exc}")
            self.bus.emit("job.failed", job_id=job.job_id, data={"reason": str(exc)})
            return {"status": "failed", "job_id": job.job_id, "error": str(exc)}
        ex.shutdown(wait=False)

        fresh = self.store.get_job(job.job_id)
        if fresh and fresh.status in ("stopped", "cancelled"):
            self.bus.emit("job.stopped", job_id=job.job_id)
            return {"status": fresh.status, "job_id": job.job_id}

        result = result if isinstance(result, dict) else {"value": result}
        self.store.save_result(job.job_id, result)

        # The handler's OWN verdict decides the job's terminal status. Before
        # this, every non-exception return was recorded as `completed`, so a
        # handler returning {"status": "blocked"} (recon on an unauthorized
        # target) or {"status": "failed"} looked like success in `wth-ai job
        # list/show`, in Telegram/Discord notifications, and for any query that
        # filters on status -- a failure that reads as a success.
        handler_status = str(result.get("status", "")).lower()
        if handler_status in ("failed", "blocked"):
            reason = str(result.get("error") or result.get("reason") or handler_status)
            self.store.fail_job(job.job_id, reason)
            self.store.append_log(job.job_id, f"failed ({handler_status}): {reason}")
            self.bus.emit(
                "job.failed", job_id=job.job_id,
                data={"reason": reason, "handler_status": handler_status},
            )
            return {"status": "failed", "job_id": job.job_id, "error": reason, "result": result}
        if handler_status == "stopped":
            self.store.update_status(job.job_id, "stopped")
            self.store.append_log(job.job_id, "stopped by handler")
            self.bus.emit("job.stopped", job_id=job.job_id)
            return {"status": "stopped", "job_id": job.job_id, "result": result}

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
