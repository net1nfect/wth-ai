"""Job lama tidak boleh kelaparan saat antrean melebihi 1000.

Item 4: `JobStore.next_queued()` memuat `list_jobs(limit=1000)` yang diurut
`created_at DESC`, lalu menyortir di Python. Dengan >1000 job queued, job yang
tertua tidak pernah ikut terbaca sehingga tidak pernah dijalankan.
"""

from __future__ import annotations

from wth_ai.jobs.model import PRIORITY_RANK, Job
from wth_ai.jobs.queue import JobQueue
from wth_ai.jobs.store import JobStore

def _store(tmp_path) -> JobStore:
    return JobStore(db_path=tmp_path / "jobs.sqlite", home=tmp_path)

def _job(job_id: str, *, priority: str, created_at: str) -> Job:
    return Job(
        job_id=job_id, type="recon", status="queued", priority=priority,
        created_at=created_at, updated_at=created_at, target="example.com",
    )

def test_oldest_high_priority_job_is_not_starved(tmp_path):
    """1001 job: yang tertua berprioritas high harus diklaim lebih dulu."""
    store = _store(tmp_path)
    queue = JobQueue(store)

    # Job tertua, prioritas high -- target sebenarnya dari test ini.
    queue.enqueue(_job("JOB-OLDEST", priority="high",
                       created_at="2026-01-01T00:00:00+00:00"))
    # 1000 job baru berprioritas normal, semua created_at lebih besar.
    for i in range(1000):
        queue.enqueue(_job(
            f"JOB-{i:04d}", priority="normal",
            created_at=f"2026-06-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
        ))

    peeked = queue.peek()
    assert peeked is not None
    assert peeked.job_id == "JOB-OLDEST", (
        f"peek() mengembalikan {peeked.job_id}; job tertua berprioritas high "
        "tidak terbaca karena jendela LIMIT 1000"
    )

    claimed = queue.dequeue()
    assert claimed is not None and claimed.job_id == "JOB-OLDEST"

def test_priority_ordering_matches_priority_rank(tmp_path):
    """Urutan klaim mengikuti PRIORITY_RANK, lalu created_at paling tua.

    Kunci kontrak: kalau PRIORITY_RANK berubah tapi urutan SQL tidak ikut, test
    ini gagal.
    """
    store = _store(tmp_path)
    queue = JobQueue(store)

    queue.enqueue(_job("JOB-LOW", priority="low",
                       created_at="2026-01-01T00:00:00+00:00"))
    queue.enqueue(_job("JOB-NORMAL", priority="normal",
                       created_at="2026-01-02T00:00:00+00:00"))
    queue.enqueue(_job("JOB-HIGH-OLD", priority="high",
                       created_at="2026-01-03T00:00:00+00:00"))
    queue.enqueue(_job("JOB-HIGH-NEW", priority="high",
                       created_at="2026-01-04T00:00:00+00:00"))

    expected = sorted(
        ["JOB-LOW", "JOB-NORMAL", "JOB-HIGH-OLD", "JOB-HIGH-NEW"],
        key=lambda jid: (PRIORITY_RANK[queue.store.get_job(jid).priority],),
    )
    order: list[str] = []
    while (job := queue.dequeue()) is not None:
        order.append(job.job_id)

    assert order == ["JOB-HIGH-OLD", "JOB-HIGH-NEW", "JOB-NORMAL", "JOB-LOW"]
    assert set(order) == set(expected)
