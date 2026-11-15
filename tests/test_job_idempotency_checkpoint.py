"""Regression tests — Job idempotency & checkpoint (prioritas #1).

Celah nyata: `Job.correlation_id` hanya field pasif (disimpan, tidak dipakai
untuk dedup), dan tidak ada mekanisme checkpoint/resume: job yang crash di
tengah harus mengulang dari awal, serta `create_job` berulang dengan intent
sama dapat membuat duplikat.

Kontrak yang diuji (additive, tidak mengubah signature lama):
* `create_job(..., idempotency_key="k")` mengembalikan job yang SAMA untuk key
  yang sama + parameter yang sama; key sama dengan intent berbeda ditolak.
* `save_checkpoint(job_id, payload)` / `load_checkpoint(job_id)` persisten.
* `retry_job` tidak menghapus checkpoint; checkpoint dapat dibaca setelah retry.
* Checkpoint korup/tidak ada -> `None` (fail-closed, tidak crash).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.store import JobStore


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


def _manager() -> JobManager:
    return JobManager(WTHConfig(), bus=EventBus())


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_same_idempotency_key_returns_same_job():
    with temp_home():
        mgr = _manager()
        a = mgr.create_job("recon", target="example.com", idempotency_key="recon-example-1")
        b = mgr.create_job("recon", target="example.com", idempotency_key="recon-example-1")
        assert a.job_id == b.job_id
        assert len(mgr.list_jobs()) == 1


def test_idempotency_key_survives_reload():
    with temp_home():
        mgr = _manager()
        a = mgr.create_job("recon", target="example.com", idempotency_key="persist-1")
        mgr2 = _manager()
        b = mgr2.create_job("recon", target="example.com", idempotency_key="persist-1")
        assert a.job_id == b.job_id


def test_same_key_different_intent_is_rejected():
    with temp_home():
        mgr = _manager()
        mgr.create_job("recon", target="example.com", idempotency_key="dup-intent")
        other = mgr.create_job("report", target="other.com", idempotency_key="dup-intent")
        # fail-closed: must NOT silently return the recon job for a report request
        assert other.status == "failed"
        assert "idempotency" in (other.error or "").lower()


def test_different_keys_create_different_jobs():
    with temp_home():
        mgr = _manager()
        a = mgr.create_job("recon", target="example.com", idempotency_key="k1")
        b = mgr.create_job("recon", target="example.com", idempotency_key="k2")
        assert a.job_id != b.job_id


def test_no_key_keeps_legacy_behavior():
    with temp_home():
        mgr = _manager()
        a = mgr.create_job("recon", target="example.com")
        b = mgr.create_job("recon", target="example.com")
        assert a.job_id != b.job_id


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_roundtrip():
    with temp_home():
        store = JobStore()
        from wth_ai.jobs.model import Job

        job = store.create_job(Job(type="recon", target="example.com"))
        store.save_checkpoint(job.job_id, {"stage": "recon", "cursor": 3})
        got = store.load_checkpoint(job.job_id)
        assert got == {"stage": "recon", "cursor": 3}


def test_checkpoint_missing_returns_none():
    with temp_home():
        store = JobStore()
        assert store.load_checkpoint("JOB-doesnotexist") is None


def test_checkpoint_corrupt_fails_closed():
    with temp_home() as home:
        store = JobStore()
        from wth_ai.jobs.model import Job

        job = store.create_job(Job(type="recon", target="example.com"))
        path = store.checkpoint_path(job.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert store.load_checkpoint(job.job_id) is None


def test_retry_preserves_checkpoint():
    with temp_home():
        mgr = _manager()
        job = mgr.create_job("custom", target="example.com")
        mgr.store.save_checkpoint(job.job_id, {"cursor": 7})
        mgr.store.fail_job(job.job_id, "boom")
        mgr.retry_job(job.job_id)
        assert mgr.store.load_checkpoint(job.job_id) == {"cursor": 7}


def test_checkpoint_written_atomically_no_partial_json():
    with temp_home():
        store = JobStore()
        from wth_ai.jobs.model import Job

        job = store.create_job(Job(type="recon", target="example.com"))
        store.save_checkpoint(job.job_id, {"a": 1})
        path = store.checkpoint_path(job.job_id)
        json.loads(path.read_text(encoding="utf-8"))  # always valid JSON
        assert not path.with_suffix(".tmp").exists()
