"""Antrean worker lokal (§1.2 poin 6): file + flock, tanpa pendengar jaringan.

Batasnya disengaja: paralelisme hanya di satu host, tidak ada scheduler remote,
dan satu job hanya boleh dipegang satu worker. Test ini menguji klaim atomik
(dua worker tidak boleh mendapat job yang sama) dan ketiadaan jalur jaringan.
"""

from __future__ import annotations

import threading
from pathlib import Path

from wth_ai.jobs.local_queue import LocalQueue


def _queue(tmp_path: Path) -> LocalQueue:
    return LocalQueue(tmp_path / "queue.json")

def test_enqueue_then_claim(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    job_id = q.enqueue({"cmd": "scan", "target": "example"})

    claimed = q.claim("w1")

    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["payload"]["cmd"] == "scan"
    assert claimed["worker"] == "w1"

def test_two_workers_never_get_the_same_job(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    first = q.enqueue({"n": 1})
    second = q.enqueue({"n": 2})

    a = q.claim("w1")
    b = q.claim("w2")

    assert a is not None and b is not None
    assert {a["id"], b["id"]} == {first, second}
    assert q.claim("w3") is None

def test_concurrent_claims_are_atomic(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    for i in range(20):
        q.enqueue({"n": i})

    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        local: list[str] = []
        while True:
            job = q.claim(name)
            if job is None:
                break
            local.append(job["id"])
        with lock:
            claimed.extend(local)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 20
    assert len(set(claimed)) == 20, "ada job diklaim dua kali"

def test_complete_frees_the_job(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    job_id = q.enqueue({"n": 1})
    assert q.claim("w1") is not None

    q.complete(job_id)

    assert q.claim("w2") is None
    assert q.result(job_id)["state"] == "done"

def test_fail_is_recorded_and_retryable(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    job_id = q.enqueue({"n": 1})
    q.claim("w1")

    q.fail(job_id, "timeout")

    assert q.result(job_id)["state"] == "failed"
    assert q.result(job_id)["error"] == "timeout"

def test_state_survives_new_instance(tmp_path: Path) -> None:
    _queue(tmp_path).enqueue({"n": 1})

    fresh = LocalQueue(tmp_path / "queue.json")

    assert fresh.pending() and fresh.pending()[0]["payload"] == {"n": 1}

def test_no_network_listener_in_module() -> None:
    from wth_ai.jobs import local_queue

    source = Path(local_queue.__file__).read_text(encoding="utf-8")
    for forbidden in ("import socket", "bind((", "listen(", "HTTPServer", "requests."):
        assert forbidden not in source, f"antrean lokal tidak boleh punya {forbidden}"

def test_lock_file_is_created(tmp_path: Path) -> None:
    q = _queue(tmp_path)
    q.enqueue({"n": 1})
    assert (tmp_path / "queue.json.lock").exists()
