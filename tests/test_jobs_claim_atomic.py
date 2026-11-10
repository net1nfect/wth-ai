"""Klaim job harus atomik: dua dequeue tidak boleh mendapat job yang sama.

Item 2: `JobQueue.dequeue()` memanggil `next_queued()` lalu `update_status()`
sebagai dua operasi terpisah, tanpa kunci. Dua worker membaca baris queued yang
sama sebelum salah satunya sempat menandainya running.
"""

from __future__ import annotations

import threading

from wth_ai.jobs.model import Job
from wth_ai.jobs.queue import JobQueue
from wth_ai.jobs.store import JobStore

def _store(tmp_path) -> JobStore:
    return JobStore(db_path=tmp_path / "jobs.sqlite", home=tmp_path)

def _job(job_id: str = "JOB-1", *, priority: str = "normal") -> Job:
    return Job(job_id=job_id, type="recon", status="queued", priority=priority,
               created_at="2026-11-10T00:00:00+00:00",
               updated_at="2026-11-10T00:00:00+00:00",
               target="example.com")

def test_second_dequeue_returns_none(tmp_path):
    """Satu job queued: dequeue pertama dapat job, kedua tidak dapat apa pun."""
    store = _store(tmp_path)
    queue = JobQueue(store)
    queue.enqueue(_job())

    first = queue.dequeue()
    second = queue.dequeue()

    assert first is not None and first.job_id == "JOB-1"
    assert first.status == "running"
    assert second is None, "job yang sudah running tidak boleh diklaim lagi"

def test_concurrent_dequeues_claim_exactly_once(tmp_path):
    """Dua koneksi ke DB yang sama, klaim bersamaan: satu pemenang saja."""
    db = tmp_path / "jobs.sqlite"
    setup = JobStore(db_path=db, home=tmp_path)
    JobQueue(setup).enqueue(_job())

    # Dua JobStore = dua koneksi terpisah, meniru dua worker di proses berbeda.
    stores = [JobStore(db_path=db, home=tmp_path) for _ in range(2)]
    results: list[Job | None] = []
    barrier = threading.Barrier(2, timeout=10)

    def claim(store: JobStore) -> None:
        barrier.wait()
        results.append(JobQueue(store).dequeue())

    threads = [threading.Thread(target=claim, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1, f"job diklaim {len(claimed)} kali, harus tepat 1"

def test_claim_leaves_no_duplicate_running_rows(tmp_path):
    """Setelah dua dequeue, hanya ada satu baris running."""
    store = _store(tmp_path)
    queue = JobQueue(store)
    queue.enqueue(_job())
    queue.dequeue()
    queue.dequeue()

    running = store.list_jobs(status="running", limit=10)
    queued = store.list_jobs(status="queued", limit=10)
    assert len(running) == 1
    assert queued == []
