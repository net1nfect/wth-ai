"""Fase 4 tests: job store, queue, worker, approval gate, policy, doctor."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.approvals import ApprovalRegistry
from wth_ai.config import WTHConfig
from wth_ai.events import EventBus
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.model import Job
from wth_ai.jobs.store import JobStore
from wth_ai.jobs.worker import WorkerEngine
from wth_ai.vault import SecretVault


@contextlib.contextmanager
def temp_home():
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        old_home = os.environ.get("WTH_HOME")
        old_cfg = cfg_mod.USER_CONFIG_PATH
        old_env = cfg_mod.USER_ENV_PATH
        os.environ["WTH_HOME"] = str(base)
        cfg_mod.USER_CONFIG_PATH = base / "config" / "user.yaml"
        cfg_mod.USER_ENV_PATH = base / ".env"
        init_workspace(base)
        try:
            yield base
        finally:
            cfg_mod.USER_CONFIG_PATH = old_cfg
            cfg_mod.USER_ENV_PATH = old_env
            if old_home is None:
                os.environ.pop("WTH_HOME", None)
            else:
                os.environ["WTH_HOME"] = old_home


def _manager(bus=None) -> JobManager:
    return JobManager(WTHConfig(), bus=bus or EventBus())


def _write_scope_card(base: Path, target: str = "example.com") -> None:
    """P0-1: handler_recon membaca scope card dari disk, bukan metadata job."""
    from wth_ai.scope_card import ScopeCard

    ScopeCard(target=target, domains=[target], authorized=True).write(
        base / "scope_card.json"
    )


# --- store ---

def test_create_and_get_job():
    with temp_home():
        store = JobStore()
        job = store.create_job(Job(type="recon", target="example.com"))
        got = store.get_job(job.job_id)
        assert got is not None
        assert got.type == "recon"
        assert got.target == "example.com"
        assert got.status == "queued"


def test_list_jobs_filter():
    with temp_home():
        store = JobStore()
        store.create_job(Job(type="recon", target="a.com"))
        store.create_job(Job(type="report", status="completed"))
        assert len(store.list_jobs()) == 2
        assert len(store.list_jobs(status="completed")) == 1
        assert len(store.list_jobs(type="recon")) == 1


def test_update_status_and_progress():
    with temp_home():
        store = JobStore()
        job = store.create_job(Job(type="recon", target="a.com"))
        running = store.update_status(job.job_id, "running")
        assert running.status == "running"
        assert running.started_at is not None
        prog = store.update_progress(job.job_id, 42, 100, "halfway")
        assert prog.progress == {"current": 42, "total": 100, "message": "halfway"}


def test_append_and_read_log():
    with temp_home():
        store = JobStore()
        job = store.create_job(Job(type="recon", target="a.com"))
        store.append_log(job.job_id, "line one")
        store.append_log(job.job_id, "line two")
        log = store.read_log(job.job_id)
        assert "line one" in log and "line two" in log


def test_save_result_writes_file():
    with temp_home() as home:
        store = JobStore()
        job = store.create_job(Job(type="recon", target="a.com"))
        store.save_result(job.job_id, {"findings": 3})
        assert store.get_job(job.job_id).result == {"findings": 3}
        assert (home / "wth-ai-results" / job.job_id / "result.json").exists()


# --- queue ---

def test_enqueue_dequeue_priority():
    with temp_home():
        mgr = _manager()
        low = mgr.create_job("custom", priority="low")
        high = mgr.create_job("custom", priority="high")
        # high priority dequeued first
        first = mgr.queue.dequeue()
        assert first.job_id == high.job_id
        assert first.status == "running"
        assert mgr.queue.dequeue().job_id == low.job_id


# --- worker ---

def test_worker_run_once_success():
    with temp_home():
        mgr = _manager()
        # This test is about the worker lifecycle, not the scope gate, so it
        # provides the real artifact the handler requires (P0-1). The gate itself
        # is covered by test_recon_job_without_scope_card_fails_closed below.
        _write_scope_card(Path(os.environ["WTH_HOME"]))
        job = mgr.create_job("recon", target="example.com")
        res = WorkerEngine(mgr).run_once()
        assert res["status"] == "completed"
        done = mgr.get_job(job.job_id)
        assert done.status == "completed"
        assert done.result.get("summary")
        assert done.progress["current"] == 100


def test_recon_job_without_scope_card_fails_closed():
    """P0-1: otorisasi dibaca dari `scope_card.json`, bukan dari metadata job.
    Sebelum ini setiap pemanggil hardcode `authorized=True`, sehingga kartu
    tidak pernah diperiksa dan `wth recon run <host apa pun>` langsung memindai.
    Tanpa kartu, job gagal SEBELUM engine dibuat."""
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("recon", target="example.com",
                             metadata={"authorized": True})  # metadata saja tidak cukup
        res = WorkerEngine(mgr).run_once()
        assert res["status"] == "failed"
        done = mgr.get_job(job.job_id)
        assert done is not None
        assert done.status == "failed"
        assert "scope" in (done.error or "").lower()


def test_recon_job_with_scope_card_runs():
    """The happy path for every real operator entry point."""
    with temp_home() as home:
        _write_scope_card(home)
        mgr = _manager()
        job = mgr.create_job("recon", target="example.com")
        res = WorkerEngine(mgr).run_once()
        assert res["status"] == "completed"


def test_worker_handles_exception_marks_failed():
    with temp_home():
        mgr = _manager()
        worker = WorkerEngine(mgr)
        worker.register("custom", lambda j, c: (_ for _ in ()).throw(RuntimeError("boom")))
        job = mgr.create_job("custom")
        res = worker.run_once()
        assert res["status"] == "failed"
        assert "boom" in mgr.get_job(job.job_id).error


def test_worker_emits_lifecycle_events():
    with temp_home() as home:
        _write_scope_card(home, "x.com")
        bus = EventBus()
        mgr = _manager(bus=bus)
        seen: list[str] = []
        bus.subscribe(lambda e: seen.append(e.type))
        mgr.create_job("recon", target="x.com")
        WorkerEngine(mgr).run_once()
        assert "job.created" in seen
        assert "job.started" in seen
        assert "job.progress" in seen
        assert "job.completed" in seen


def test_stop_job():
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("custom")
        stopped = mgr.stop_job(job.job_id)
        assert stopped.status == "stopped"
        # stopped job is not runnable
        assert WorkerEngine(mgr).run_once()["status"] == "idle"


def test_retry_job():
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("custom")
        mgr.store.fail_job(job.job_id, "boom")
        retried = mgr.retry_job(job.job_id)
        assert retried.status == "queued"
        assert retried.error is None


# --- approval gate ---

def test_approval_gate_approved_continues():
    with temp_home() as home:
        # The scope card is provided so these tests exercise the APPROVAL gate,
        # not the scope gate -- otherwise the denial test would pass for the
        # wrong reason.
        _write_scope_card(home, "x.com")
        bus = EventBus()
        mgr = _manager(bus=bus)
        seen: list[str] = []
        bus.subscribe(lambda e: seen.append(e.type))
        job = mgr.create_job("recon", target="x.com", requires_approval=True)
        worker = WorkerEngine(mgr)
        first = worker.run_once()
        assert first["status"] == "waiting_approval"
        assert mgr.get_job(job.job_id).status == "waiting_approval"
        # approve and run again
        ApprovalRegistry().approve(first["approval_id"])
        second = worker.run_once()
        assert second["status"] == "completed"
        assert "job.approval_required" in seen
        assert "job.approved" in seen
        assert "job.completed" in seen


def test_approval_gate_denied_fails():
    with temp_home():
        bus = EventBus()
        mgr = _manager(bus=bus)
        seen: list[str] = []
        bus.subscribe(lambda e: seen.append(e.type))
        # `authorized` is set explicitly so these tests exercise the APPROVAL
        # gate, not the (now fail-closed) authorization gate -- otherwise the
        # denial test would pass for the wrong reason.
        job = mgr.create_job(
            "recon", target="x.com", requires_approval=True, metadata={"authorized": True},
        )
        worker = WorkerEngine(mgr)
        first = worker.run_once()
        ApprovalRegistry().deny(first["approval_id"])
        worker.run_once()  # resume -> denied -> failed
        final = mgr.get_job(job.job_id)
        assert final.status == "failed"
        assert "approval denied" in final.error
        assert "job.denied" in seen
        assert "job.failed" in seen


# --- policy ---

def test_policy_reject_prevents_enqueue():
    with temp_home():
        bus = EventBus()
        mgr = _manager(bus=bus)
        seen: list[str] = []
        bus.subscribe(lambda e: seen.append(e.type))
        job = mgr.create_job("recon", target=None)  # recon requires target
        assert job.status == "failed"
        assert "policy" in job.error
        assert mgr.list_jobs(status="queued") == []
        assert "app.error" in seen


def test_policy_forbidden_target():
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("recon", target="prod.internal", forbidden=("internal",))
        assert job.status == "failed"
        assert "forbidden" in job.error


# --- secret hygiene ---

def test_no_secret_plaintext_in_job_artifacts():
    with temp_home() as home:
        SecretVault().set("openrouter_api_key", "sk-LEAK-SECRET-123")
        _write_scope_card(home, "x.com")
        mgr = _manager()
        job = mgr.create_job("recon", target="x.com")
        WorkerEngine(mgr).run_once()
        log = mgr.logs(job.job_id)
        result_file = (home / "wth-ai-results" / job.job_id / "result.json").read_text()
        assert "sk-LEAK-SECRET-123" not in log
        assert "sk-LEAK-SECRET-123" not in result_file


# --- doctor ---

def test_doctor_jobs_ok():
    with temp_home():
        from wth_ai.tools.doctor import ToolDoctor
        res = ToolDoctor(WTHConfig.load()).check_jobs()
        assert res["ok"] is True
        assert res["schema_ok"] is True


def test_doctor_detects_broken_job_store():
    with temp_home() as home:
        # Make the db path a directory so sqlite cannot open it.
        (home / "data").mkdir(parents=True, exist_ok=True)
        (home / "data" / "jobs.sqlite").mkdir()
        from wth_ai.tools.doctor import ToolDoctor
        res = ToolDoctor(WTHConfig.load()).check_jobs()
        assert res["ok"] is False


def test_doctor_full_report_has_jobs_section():
    with temp_home():
        from wth_ai.tools.doctor import ToolDoctor
        report = ToolDoctor(WTHConfig.load()).full_report()
        assert "Jobs & Worker" in report


# --- bot command service reads real jobs ---

def test_command_service_reads_jobs():
    with temp_home():
        from wth_ai.integrations.commands import BotCommandService
        mgr = _manager()
        mgr.create_job("recon", target="x.com")
        svc = BotCommandService(WTHConfig(), jobs=mgr)
        out = svc.handle("jobs")
        assert "recon" in out
        status = svc.handle("status")
        assert "jobs:" in status


# --- CLI smoke ---

def test_cli_job_create_and_list():
    with temp_home() as home:
        env = {**os.environ, "WTH_HOME": str(home), "WTH_SKIP_SETUP": "1"}
        root = Path(__file__).resolve().parents[1]
        created = subprocess.run(
            [sys.executable, str(root / "wth-ai-main.py"), "job", "create", "recon", "--target", "example.com"],
            capture_output=True, text=True, env=env, cwd=str(root),
        )
        assert "Created" in created.stdout, created.stderr
        listed = subprocess.run(
            [sys.executable, str(root / "wth-ai-main.py"), "job", "list"],
            capture_output=True, text=True, env=env, cwd=str(root),
        )
        assert "recon" in listed.stdout
