"""Fase 6 P5 tests: executor job handler end-to-end (offline)."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")

import pytest

from wth_ai.recon import dns, tls
from wth_ai.recon import http as rhttp


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.events import reset_event_bus
    from wth_ai.workspace import init_workspace
    reset_event_bus()
    init_workspace()
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(
        url=rhttp.normalize_url(url), status=200,
        headers={"Content-Type": "text/html", "Server": "nginx"},
        text="<html><body><a href='/login'>x</a><a href='/api/v1/users?id=1'>u</a></body></html>"))
    monkeypatch.setattr(dns, "resolve_host", lambda h: [{"record_type": "A", "value": "1.2.3.4"}])
    monkeypatch.setattr(tls, "cert_info", lambda h, port=443, timeout=8: {
        "subject": ((("commonName", h),),), "issuer": ((("commonName", "CA"),),),
        "notAfter": "Jan 1 2030", "subjectAltName": (("DNS", h),)})
    return True


def test_executor_blocked_in_passive_mode(tmp_home):
    from wth_ai.jobs.manager import JobManager
    mgr = JobManager()
    job = mgr.create_job("executor", target="example.com", execution_mode="passive-only",
                         metadata={"authorized": True})
    assert job.status == "failed"
    assert "forbids active" in job.error


def test_executor_handler_completes(tmp_home, offline):
    from wth_ai.jobs.manager import JobManager
    from wth_ai.jobs.worker import WorkerEngine

    mgr = JobManager()
    job = mgr.create_job("executor", target="example.com", execution_mode="safe-active",
                         metadata={"authorized": True})
    assert job.status == "queued"
    WorkerEngine(mgr).run(drain=True)
    done = mgr.get_job(job.job_id)
    assert done.status == "completed"
    assert done.result.get("status") == "completed"
    out = Path(mgr.store.results_dir) / job.job_id
    assert (out / "exec_manifest.json").exists()
    assert (out / "exec_runs.jsonl").exists()
    assert (out / "exec_findings.json").exists()


def test_executor_reuses_recon_job(tmp_home, offline):
    from wth_ai.jobs.manager import JobManager
    from wth_ai.jobs.worker import WorkerEngine

    mgr = JobManager()
    recon = mgr.create_job("recon", target="example.com", metadata={"authorized": True})
    WorkerEngine(mgr).run(drain=True)
    ex = mgr.create_job("executor", target="example.com", execution_mode="safe-active",
                        metadata={"authorized": True, "recon_job_id": recon.job_id})
    WorkerEngine(mgr).run(drain=True)
    done = mgr.get_job(ex.job_id)
    assert done.status == "completed"
    assert "stats" in done.result
