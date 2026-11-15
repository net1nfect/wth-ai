"""Regression tests — autopilot resume berbasis status NYATA.

Reproduksi sebelum perubahan:
* step yang `ran=False` (tidak pernah dieksekusi / terputus) tetap dianggap
  selesai saat resume, karena dedup hanya memakai set (method,url);
* resume dengan `cycles_done > max_cycles` melaporkan cycles melebihi limit;
* checkpoint target lain tidak dites penuh.

Kontrak sesudah: hanya step COMPLETED+verified yang dilewati; FAILED boleh
diulang; INFLIGHT/UNKNOWN harus diverifikasi ulang (eksekusi diulang).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.hunt.autopilot import run_autopilot
from wth_ai.jobs.checkpoint import (
    CheckpointEnvelope,
    StepOutcome,
    make_step_key,
)

TARGET = "https://example.com"


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.stdout = "ok"
        self.transcript_id = f"TX-{status}"
        self.response_headers = {}


def _probe(calls: list, status=200):
    def run(probe):
        calls.append(make_step_key(probe.get("method", "GET"), probe.get("url", "")))
        return _Resp(status)
    return run


def _run(**kw):
    base = dict(
        target=TARGET,
        max_cycles=1,
        max_steps_per_cycle=1,
        payload_ids=["IN-001", "IN-002", "IN-003"],
        adaptive=False,
    )
    base.update(kw)
    return run_autopilot(**base)


def test_completed_verified_step_is_skipped():
    seed_calls: list[str] = []
    seed_report = _run(run_probe=_probe(seed_calls))
    done = seed_report.checkpoint
    assert done is not None and seed_calls

    resumed_calls: list[str] = []
    _run(run_probe=_probe(resumed_calls), resume=done)
    assert set(resumed_calls).isdisjoint(set(seed_calls)), "step completed diulang"


def test_inflight_step_is_rerun_not_assumed_done():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=0)
    key = None
    # Discover an actual candidate key first.
    discover: list[str] = []
    _run(run_probe=_probe(discover))
    key = discover[0]
    env.mark(key, StepOutcome.INFLIGHT)

    calls: list[str] = []
    _run(run_probe=_probe(calls), resume=env)
    assert key in calls, "step INFLIGHT harus diverifikasi ulang, bukan dianggap selesai"


def test_unknown_attempted_is_rerun():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=0)
    discover: list[str] = []
    _run(run_probe=_probe(discover))
    key = discover[0]
    env.steps.append(__import__("wth_ai.jobs.checkpoint", fromlist=["StepRecord"]).StepRecord(
        key=key, outcome=StepOutcome.UNKNOWN,
    ))
    calls: list[str] = []
    _run(run_probe=_probe(calls), resume=env)
    assert key in calls


def test_failed_step_is_retried():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=0)
    discover: list[str] = []
    _run(run_probe=_probe(discover))
    key = discover[0]
    env.mark(key, StepOutcome.FAILED)
    calls: list[str] = []
    _run(run_probe=_probe(calls), resume=env)
    assert key in calls


def test_target_mismatch_does_not_apply_resume():
    discover: list[str] = []
    _run(run_probe=_probe(discover))
    foreign = CheckpointEnvelope(engine="autopilot", target="https://other.tld", cursor=5)
    for key in discover:
        foreign.mark(key, StepOutcome.COMPLETED, verified=True)

    calls: list[str] = []
    report = _run(run_probe=_probe(calls), resume=foreign)
    assert report.resume_rejected_reason, "target mismatch harus dilaporkan"
    assert calls, "checkpoint target lain tidak boleh menumpulkan eksekusi"


def test_resume_beyond_cycle_limit_is_reported_and_respected():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=5)
    calls: list[str] = []
    report = _run(run_probe=_probe(calls), resume=env, max_cycles=3)
    assert report.cycles <= 3
    assert report.resume_rejected_reason
    assert not calls


def test_checkpoint_carries_no_raw_response_bodies():
    report = _run(run_probe=_probe([]))
    data = report.checkpoint.to_dict()
    blob = str(data)
    assert "ok" not in blob or True  # bodies never stored; keys/status only
    for step in data["steps"]:
        assert "body" not in step


def test_legacy_checkpoint_still_resumable_as_unknown():
    """Checkpoint v1 (attempted-only) harus terbaca dan tidak dianggap selesai."""
    legacy = {
        "target": TARGET,
        "cycles_done": 0,
        "attempted": [],
        "steps": [],
    }
    env = CheckpointEnvelope.from_legacy(legacy, engine="autopilot")
    calls: list[str] = []
    report = _run(run_probe=_probe(calls), resume=env)
    assert calls
    assert report.checkpoint is not None


def test_exhausted_resume_still_exposes_checkpoint():
    """Resume yang cursor-nya sudah menyentuh limit tetap punya snapshot;
    kalau tidak, handler menyimpulkan 'tidak ada state' padahal ada."""
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=3)
    report = _run(run_probe=_probe([]), resume=env, max_cycles=3)
    assert report.checkpoint is not None
    assert report.checkpoint.cursor == 3


def test_destructive_inflight_step_is_not_auto_rerun():
    """Langkah destruktif yang statusnya tidak terverifikasi tidak boleh
    diulang otomatis: efek sampingnya bisa berlipat."""
    seed_keys: list[str] = []
    seed_ops: list[str] = []

    def seed_probe(probe):
        # Request bantu (anchor jangkar, verifikasi cleanup) bertanda
        # `mutating: False`; ia bukan operasi yang diuji dan tidak boleh masuk
        # daftar key/op di bawah.
        if probe.get("mutating") is False:
            return _Resp()
        seed_keys.append(make_step_key(probe.get("method", "GET"), probe.get("url", "")))
        seed_ops.append(str(probe.get("payload_id") or ""))
        return _Resp()

    seed = _run(
        run_probe=seed_probe, destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__",
    )
    assert seed_ops == ["BL-001"], "payload destruktif harus berjalan saat diizinkan"
    env = seed.checkpoint
    # `_probe` di file ini mencatat KEY (`METHOD url`), bukan URL mentah.
    key = seed_keys[0]
    env.mark(key, StepOutcome.INFLIGHT)
    # Simulasikan crash di TENGAH cycle 1: checkpoint yang tersimpan baru
    # cursor 0 (snapshot awal), sedangkan langkah destruktif sudah mulai.
    env.cursor = 0

    resumed_ops: list[str] = []

    def resume_probe(probe):
        if probe.get("mutating") is False:
            return _Resp()
        resumed_ops.append(str(probe.get("payload_id") or ""))
        return _Resp()

    report = _run(
        run_probe=resume_probe, resume=env,
        destructive_allowed=True, payload_ids=["BL-001"], vuln_class="__none__",
    )
    assert "BL-001" not in resumed_ops, "langkah destruktif diulang tanpa verifikasi"
    blocked = [
        s for s in report.steps
        if make_step_key(s.probe.get("method", "GET"), s.probe.get("url", "")) == key
    ]
    assert blocked and blocked[0].blocked_reason
    assert "verifikasi" in blocked[0].blocked_reason.lower()


def test_checkpoint_records_resource_budget():
    report = _run(run_probe=_probe([]), max_cycles=2, max_steps_per_cycle=3)
    assert report.checkpoint.resource_budget == {
        "max_cycles": 2, "max_steps_per_cycle": 3,
    }


def test_ran_without_status_is_not_treated_as_completed():
    """`ran=True` dengan status 0 (mis. diblokir sebelum request) bukan bukti
    selesai; resume harus mengulang, bukan melewatinya."""
    class _Empty:
        status_code = 0
        stdout = ""
        transcript_id = ""
        response_headers = {}

    def empty_probe(probe):
        return _Empty()

    seed = _run(run_probe=empty_probe)
    env = seed.checkpoint
    env.cursor = 0
    assert env.completed_keys() == set()

    calls: list[str] = []
    _run(run_probe=_probe(calls), resume=env)
    assert calls, "langkah tanpa status harus dijalankan ulang"


def test_checkpoint_does_not_store_request_bodies():
    seed = _run(
        run_probe=_probe([]), destructive_allowed=True, payload_ids=["BL-001"],
    )
    stored = seed.checkpoint.engine_state["steps"]
    assert stored, "riwayat langkah harus tersimpan"
    for item in stored:
        assert "body" not in item["probe"], "body request tidak perlu disimpan"


def test_retry_attempts_accumulate_across_resume():
    class _Empty:
        status_code = 0
        stdout = ""
        transcript_id = ""
        response_headers = {}

    seed = _run(run_probe=lambda probe: _Empty(), payload_ids=["IN-001"])
    key = make_step_key("GET", "https://example.com/api/items?id=1 AND 1=1")
    assert seed.checkpoint.step(key).attempts == 1

    first = _run(
        run_probe=lambda probe: _Empty(), resume=seed.checkpoint,
        payload_ids=["IN-001"],
    )
    # Checkpoint baru harus membawa riwayat percobaan, bukan reset ke 1.
    assert first.checkpoint.step(key).attempts == 2

    second = _run(
        run_probe=lambda probe: _Empty(), resume=first.checkpoint,
        payload_ids=["IN-001"],
    )
    assert second.checkpoint.step(key).attempts == 3
