"""Cron scheduler tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from wth_ai.cron.scheduler import CronScheduler
from wth_ai.cron.store import CronStore


def test_cron_add_and_list(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    store = CronStore()
    entry = store.add("recon", "example.com", "every 6h")
    assert entry.cron_id
    assert len(store.list()) == 1


def test_cron_tick_enqueues_recon(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    store = CronStore()
    store.add("recon", "example.com", "every 6h")
    job = MagicMock()
    job.job_id = "job-1"
    mgr = MagicMock()
    mgr.create_job.return_value = job
    sched = CronScheduler(mgr, store=store)
    fired = sched.tick()
    assert fired
    mgr.create_job.assert_called_once()


def test_cron_not_due_before_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    store = CronStore()
    entry = store.add("recon", "example.com", "every 6h")
    store.update_run(entry.cron_id)
    mgr = MagicMock()
    sched = CronScheduler(mgr, store=store)
    assert sched.tick() == []


def test_cron_store_lock_is_exclusive(tmp_path):
    import threading
    import time

    from wth_ai.cron.store import CronStore

    store = CronStore(path=tmp_path / "cron.json")
    inside = {"n": 0}
    overlap: list[int] = []

    def worker() -> None:
        with store.locked():
            inside["n"] += 1
            if inside["n"] > 1:
                overlap.append(inside["n"])
            time.sleep(0.05)
            inside["n"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlap == []


def test_cron_mutators_take_lock_and_write_atomically(tmp_path, monkeypatch):
    import contextlib
    import pathlib

    from wth_ai.cron.store import CronStore

    store = CronStore(path=tmp_path / "cron.json")
    entered: list[int] = []

    @contextlib.contextmanager
    def rec():
        entered.append(1)
        yield

    def boom(self, *a, **k):
        raise AssertionError("cron store harus menulis atomik")

    monkeypatch.setattr(store, "locked", rec)
    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    store.add("recon", "example.com", "every 6h")
    assert entered
    assert "example.com" in (tmp_path / "cron.json").read_text(encoding="utf-8")
