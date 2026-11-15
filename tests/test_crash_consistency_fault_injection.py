"""Fault-injection deterministik — crash consistency & recovery autopilot.

Setiap skenario memeriksa EMPAT hal, bukan hanya "exception muncul":
1. state yang benar-benar dipersistensikan (snapshot checkpoint terakhir),
2. keadaan target simulasi (mutasi yang sudah terjadi),
3. keputusan resume (dijalankan ulang / diblokir),
4. hasil akhir yang dilaporkan ke operator.

Target selalu simulasi; tidak ada jaringan.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.hunt.autopilot import probe_operation_id, run_autopilot
from wth_ai.jobs.checkpoint import (
    CheckpointEnvelope,
    CheckpointValidationError,
    StepOutcome,
    make_step_key,
)
from wth_ai.jobs.model import Job
from wth_ai.jobs.store import JobStore

TARGET = "https://example.com"
CART = "https://example.com/api/cart"


class _Crash(BaseException):
    """Mensimulasikan proses mati: tidak ditangkap `except Exception`."""


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status
        self.stdout = "ok"
        self.transcript_id = f"TX-{status}"
        self.response_headers = {}


class _Checkpoints:
    """Callback checkpoint in-memory yang bisa gagal/„crash" pada panggilan ke-N."""

    def __init__(self, *, fail_at: set[int] | None = None, crash_at: int | None = None) -> None:
        self.saved: list[dict] = []
        self.calls = 0
        self.fail_at = fail_at or set()
        self.crash_at = crash_at

    def __call__(self, env: CheckpointEnvelope) -> None:
        self.calls += 1
        if self.crash_at is not None and self.calls == self.crash_at:
            raise _Crash(f"crash at checkpoint call {self.calls}")
        if self.calls in self.fail_at:
            raise OSError("disk full")
        self.saved.append(env.to_dict())

    @property
    def last(self) -> CheckpointEnvelope:
        assert self.saved, "tidak ada checkpoint yang berhasil dipersistensikan"
        return CheckpointEnvelope.from_dict(self.saved[-1])


def _record(calls: list[dict]):
    """Catat hanya request OPERASI.

    Verifier jangkar melakukan request read-only tambahan (anchor sebelum dan
    sesudah operasi, lalu cleanup). Request itu bukan langkah yang sedang
    diuji, jadi test menandainya dengan `mutating: False` dan melewatinya —
    tanpa ini, assertion "hanya BL-001 yang dikirim" menjadi ambigu antara
    "operasi tidak diulang" dan "anchor tidak dipanggil".
    """
    def run(probe: dict):
        if probe.get("mutating") is False:
            return _Resp()
        calls.append(dict(probe))
        return _Resp()
    return run


def _run(**kw):
    base = dict(
        target=TARGET,
        max_cycles=1,
        max_steps_per_cycle=1,
        adaptive=False,
    )
    base.update(kw)
    return run_autopilot(**base)


# ---------------------------------------------------------------------------
# identitas operasi
# ---------------------------------------------------------------------------


def test_operation_identity_distinguishes_same_url_payloads():
    calls: list[dict] = []
    report = _run(
        run_probe=_record(calls),
        destructive_allowed=True,
        payload_ids=["BL-001", "BL-002"],
        max_steps_per_cycle=2,
    )
    assert [p["payload_id"] for p in calls] == ["BL-001", "BL-002"]
    env = report.checkpoint
    assert env is not None
    key = make_step_key("POST", CART)
    records = [r for r in env.steps if r.key == key]
    assert len(records) == 2, "dua operasi berbeda dengan URL sama harus punya record terpisah"
    assert len({r.operation_id for r in records}) == 2
    assert all(r.operation_id for r in records)


def test_completed_same_url_operation_does_not_skip_sibling_on_resume():
    calls: list[dict] = []
    seed = _run(
        run_probe=_record(calls),
        destructive_allowed=True,
        payload_ids=["BL-001", "BL-002"],
        max_steps_per_cycle=1,
    )
    assert [p["payload_id"] for p in calls] == ["BL-001"]
    env = seed.checkpoint
    assert env is not None
    key = make_step_key("POST", CART)
    first = next(r for r in env.steps if r.key == key and "BL-001" in r.operation_id)
    # Tandai BL-001 selesai; BL-002 TIDAK boleh ikut terlewat.
    env.mark(first.key, StepOutcome.COMPLETED, verified=True, operation_id=first.operation_id)

    resumed: list[dict] = []
    _run(
        run_probe=_record(resumed),
        destructive_allowed=True,
        payload_ids=["BL-001", "BL-002"],
        max_cycles=2,
        max_steps_per_cycle=1,
        resume=env,
    )
    assert [p["payload_id"] for p in resumed] == ["BL-002"]


def test_duplicate_candidates_are_not_executed_twice_in_one_run():
    calls: list[dict] = []
    _run(
        run_probe=_record(calls),
        destructive_allowed=True,
        payload_ids=["BL-001", "BL-002"],
        max_steps_per_cycle=3,
    )
    ids = [p.get("payload_id") for p in calls]
    assert ids[:2] == ["BL-001", "BL-002"]
    assert len(ids) == len(set(ids)), f"operasi yang sama dijalankan dua kali: {ids}"


# ---------------------------------------------------------------------------
# crash di setiap batas operasi
# ---------------------------------------------------------------------------


def test_inflight_is_persisted_before_operation_starts():
    def crash_before_send(_probe: dict):
        raise _Crash("mati sebelum request")

    store = _Checkpoints()
    with pytest.raises(_Crash):
        _run(
            run_probe=crash_before_send,
            destructive_allowed=True,
            payload_ids=["BL-001"],
            checkpoint=store,
        )

    rec = store.last.step(make_step_key("POST", CART))
    assert rec is not None, "checkpoint INFLIGHT harus sudah tersimpan sebelum operasi"
    assert rec.outcome == StepOutcome.INFLIGHT
    assert rec.verified is False
    assert store.calls >= 2, "snapshot awal + snapshot INFLIGHT"


def test_crash_after_target_mutation_does_not_autorerun_destructive():
    target = {"mutated": False}

    def mutate_then_crash(probe: dict):
        # Anchor/cleanup read-only tidak boleh dianggap "operasi": crash di
        # sini mensimulasikan mati setelah MUTASI, bukan mati saat membaca.
        if probe.get("mutating") is False:
            return _Resp()
        if probe.get("payload_id") == "BL-001":
            target["mutated"] = True
        raise _Crash("mati setelah mutasi, sebelum respons")

    store = _Checkpoints()
    with pytest.raises(_Crash):
        _run(
            run_probe=mutate_then_crash,
            destructive_allowed=True,
            payload_ids=["BL-001"],
            vuln_class="__none__",
            checkpoint=store,
        )
    assert target["mutated"] is True

    persisted = store.last
    rec = persisted.step(make_step_key("POST", CART))
    assert rec is not None and rec.outcome == StepOutcome.INFLIGHT

    resumed: list[dict] = []
    report = _run(
        run_probe=_record(resumed),
        destructive_allowed=True,
        payload_ids=["BL-001"],
        vuln_class="__none__",
        resume=persisted,
    )
    assert resumed == [], "operasi destruktif dengan hasil tidak diketahui tidak boleh diulang"
    blocked = [
        s for s in report.steps
        if make_step_key(s.probe.get("method", "GET"), s.probe.get("url", ""))
        == make_step_key("POST", CART)
    ]
    assert blocked and blocked[0].blocked_reason
    assert "verifikasi" in blocked[0].blocked_reason.lower()


def test_crash_after_response_before_result_checkpoint_keeps_inflight_evidence():
    target = {"mutated": False}

    def mutate_and_respond(probe: dict):
        target["mutated"] = True
        return _Resp(200)

    # call 1 = snapshot awal, call 2 = INFLIGHT, call 3 = hasil (crash).
    store = _Checkpoints(crash_at=3)
    with pytest.raises(_Crash):
        _run(
            run_probe=mutate_and_respond,
            destructive_allowed=True,
            payload_ids=["BL-001"],
            vuln_class="__none__",
            checkpoint=store,
        )
    assert target["mutated"] is True
    persisted = store.last
    rec = persisted.step(make_step_key("POST", CART))
    assert rec is not None and rec.outcome == StepOutcome.INFLIGHT

    resumed: list[dict] = []
    _run(
        run_probe=_record(resumed),
        destructive_allowed=True,
        payload_ids=["BL-001"],
        vuln_class="__none__",
        resume=persisted,
    )
    assert resumed == [], "respons yang belum diverifikasi tidak menjadikan langkah aman diulang"


def test_checkpoint_write_failure_prevents_destructive_execution():
    calls: list[dict] = []
    store = _Checkpoints(fail_at=set(range(1, 20)))
    report = _run(
        run_probe=_record(calls),
        destructive_allowed=True,
        payload_ids=["BL-001"],
        checkpoint=store,
    )
    assert calls == [], "langkah destruktif tidak boleh jalan tanpa checkpoint yang bisa ditulis"
    assert report.checkpoint_error, "kegagalan persistence harus terlihat"
    assert "checkpoint" in report.stopped_reason.lower()
    assert report.stop_incomplete is True
    blocked = [s for s in report.steps if s.blocked_reason]
    assert blocked and "checkpoint" in blocked[0].blocked_reason.lower()


def test_checkpoint_write_failure_still_allows_non_destructive_run():
    """Kontrak lama dipertahankan: checkpoint best-effort untuk langkah aman."""
    calls: list[dict] = []
    store = _Checkpoints(fail_at=set(range(1, 20)))
    report = _run(
        run_probe=_record(calls),
        payload_ids=["IN-001"],
        max_steps_per_cycle=1,
        checkpoint=store,
    )
    assert calls, "langkah non-destruktif tetap berjalan"
    assert report.checkpoint_error
    assert report.stop_incomplete is False


# ---------------------------------------------------------------------------
# transisi status eksplisit
# ---------------------------------------------------------------------------


def test_illegal_transition_requires_explicit_reopen():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET)
    key = "GET https://example.com/x"
    env.mark(key, StepOutcome.COMPLETED, verified=True)
    with pytest.raises(CheckpointValidationError):
        env.mark(key, StepOutcome.INFLIGHT)

    rec = env.reopen(key, reason="verifikasi awal salah; operasi perlu diuji ulang")
    assert rec.outcome == StepOutcome.INFLIGHT
    assert rec.verified is False
    assert "verifikasi awal salah" in rec.note
    env.mark(key, StepOutcome.COMPLETED, verified=True)
    assert env.should_skip(key)


def test_verified_completed_cannot_be_downgraded_silently():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET)
    env.mark("GET x", StepOutcome.COMPLETED, verified=True)
    with pytest.raises(CheckpointValidationError):
        env.mark("GET x", StepOutcome.COMPLETED, verified=False)


# ---------------------------------------------------------------------------
# resume: budget, payload invalid, stop, duplikasi
# ---------------------------------------------------------------------------


def test_resource_budget_change_is_reported():
    env = CheckpointEnvelope(
        engine="autopilot", target=TARGET, cursor=0,
        resource_budget={"max_cycles": 2, "max_steps_per_cycle": 1},
    )
    calls: list[dict] = []
    report = _run(
        run_probe=_record(calls),
        resume=env,
        max_cycles=4,
        max_steps_per_cycle=2,
    )
    assert report.resource_budget_changed
    assert "max_cycles" in report.resource_budget_changed
    assert "max_steps_per_cycle" in report.resource_budget_changed


def test_invalid_engine_step_outcome_is_rejected_on_load():
    data = CheckpointEnvelope(
        engine="autopilot", target=TARGET, cursor=0,
    ).to_dict()
    data["engine_state"] = {
        "steps": [{
            "kind": "payload",
            "probe": {"method": "GET", "url": "https://example.com/x"},
            "outcome": "banana",
        }],
    }
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


def test_should_stop_callback_halts_before_next_step():
    calls: list[dict] = []
    report = _run(
        run_probe=_record(calls),
        payload_ids=["IN-001", "IN-002", "IN-003"],
        max_steps_per_cycle=3,
        should_stop=lambda: len(calls) >= 1,
    )
    assert len(calls) == 1, "setelah sinyal stop, tidak ada step baru yang dijalankan"
    assert report.stop_incomplete is True
    assert "dihentikan" in report.stopped_reason.lower()


def test_repeated_resume_from_same_checkpoint_is_stable():
    seed_calls: list[dict] = []
    seed = _run(run_probe=_record(seed_calls), payload_ids=["IN-001", "IN-002"])
    env = seed.checkpoint
    assert env is not None

    first_calls: list[dict] = []
    _run(run_probe=_record(first_calls), payload_ids=["IN-001", "IN-002"], resume=env)
    second_calls: list[dict] = []
    _run(run_probe=_record(second_calls), payload_ids=["IN-001", "IN-002"], resume=env)
    assert first_calls == second_calls == []


def test_legacy_completed_key_still_skips_without_operation_id():
    """Checkpoint v2 lama tanpa operation_id memakai fallback key (kompatibel)."""
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=0)
    env.mark(make_step_key("POST", CART), StepOutcome.COMPLETED, verified=True)
    calls: list[dict] = []
    _run(
        run_probe=_record(calls),
        destructive_allowed=True,
        payload_ids=["BL-001"],
        vuln_class="__none__",
        resume=env,
    )
    assert calls == []


def test_store_roundtrip_preserves_operation_identity(tmp_path):
    store = JobStore(home=tmp_path)
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=1)
    env.mark(
        make_step_key("POST", CART), StepOutcome.INFLIGHT,
        operation_id="POST https://example.com/api/cart#payload:BL-001",
    )
    store.save_checkpoint("JOB-op", env.to_dict())
    restored = CheckpointEnvelope.from_dict(store.load_checkpoint("JOB-op") or {})
    assert restored.steps[0].operation_id.endswith("BL-001")
    assert restored.steps[0].identity.endswith("BL-001")


# ---------------------------------------------------------------------------
# duplikasi eksekusi job
# ---------------------------------------------------------------------------


def test_two_workers_cannot_claim_the_same_job(tmp_path):
    store = JobStore(home=tmp_path)
    job = store.create_job(Job(type="custom"))
    barrier = threading.Barrier(2)

    def claim() -> str | None:
        barrier.wait(timeout=5)
        claimed = store.claim_next_queued()
        return claimed.job_id if claimed else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(claim), pool.submit(claim)]]

    assert results.count(job.job_id) == 1, f"job yang sama diklaim lebih dari sekali: {results}"


def test_probe_operation_id_is_stable_and_operation_specific():
    cart = {"method": "POST", "url": CART, "payload_id": "BL-001"}
    assert probe_operation_id(cart) == probe_operation_id(dict(cart))
    other = {"method": "POST", "url": CART, "payload_id": "BL-002"}
    assert probe_operation_id(cart) != probe_operation_id(other)


# ---------------------------------------------------------------------------
# batas identitas & migrasi checkpoint lama
# ---------------------------------------------------------------------------


def test_unknown_destructive_does_not_block_sibling_with_same_key():
    """BL-001 unverified TIDAK boleh memblokir BL-002 yang belum pernah jalan.

    Key `POST /api/cart` sama, tetapi operasinya berbeda. Memblokir berdasarkan
    key saja akan menahan seluruh katalog business-logic setelah satu langkah
    destruktif berakhir UNKNOWN.
    """
    seed_calls: list[dict] = []
    seed = _run(
        run_probe=_record(seed_calls),
        destructive_allowed=True,
        payload_ids=["BL-001", "BL-002"],
        vuln_class="__none__",
        max_steps_per_cycle=1,
    )
    assert [p["payload_id"] for p in seed_calls] == ["BL-001"]
    env = seed.checkpoint
    assert env is not None
    first_rec = env.step_by_identity(
        probe_operation_id({"method": "POST", "url": CART, "payload_id": "BL-001"})
    )
    assert first_rec is not None and first_rec.outcome == StepOutcome.UNKNOWN

    resumed: list[dict] = []
    _run(
        run_probe=_record(resumed),
        destructive_allowed=True,
        payload_ids=["BL-001", "BL-002"],
        vuln_class="__none__",
        max_cycles=2,
        max_steps_per_cycle=1,
        resume=env,
    )
    assert [p["payload_id"] for p in resumed] == ["BL-002"]


def test_legacy_v1_attempted_with_status_is_not_promoted_to_completed():
    """Checkpoint v1 hanya membuktikan "pernah dicoba", bukan "selesai"."""
    from wth_ai.hunt.autopilot import AutopilotCheckpoint

    key = "GET https://example.com/legacy"
    legacy = {
        "target": TARGET,
        "cycles_done": 0,
        "attempted": [["GET", "https://example.com/legacy"]],
        "steps": [{
            "kind": "proactive",
            "title": "legacy",
            "probe": {"method": "GET", "url": "https://example.com/legacy"},
            "ran": True,
            "status": 200,
        }],
    }
    env = AutopilotCheckpoint.from_dict(legacy)
    saved = _Checkpoints()
    _run(
        run_probe=_record([]),
        resume=env,
        checkpoint=saved,
        vuln_class="__none__",
    )
    rec = saved.last.step(key)
    assert rec is not None
    assert rec.outcome == StepOutcome.UNKNOWN
    assert rec.verified is False
    assert saved.last.should_skip(key) is False


def test_completed_record_survives_missing_outcome_in_engine_state():
    """Merge resume tidak boleh menurunkan COMPLETED menjadi UNKNOWN."""
    key = make_step_key("GET", "https://example.com/kept")
    env = CheckpointEnvelope(
        engine="autopilot", target=TARGET, cursor=1,
        resource_budget={"max_cycles": 1, "max_steps_per_cycle": 1},
    )
    env.mark(key, StepOutcome.COMPLETED, verified=True)
    env.set_engine_state({"steps": [{
        "kind": "proactive",
        "title": "kept",
        "probe": {"method": "GET", "url": "https://example.com/kept"},
        "ran": True,
        "status": 200,
    }]})

    saved = _Checkpoints()
    _run(run_probe=_record([]), resume=env, checkpoint=saved)
    rec = saved.last.step(key)
    assert rec is not None
    assert rec.outcome == StepOutcome.COMPLETED
    assert rec.verified is True
    assert saved.last.should_skip(key) is True

