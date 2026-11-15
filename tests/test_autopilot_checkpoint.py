"""Regression tests — autopilot checkpoint/resume (ADR-014 follow-up).

Bug: `handler_autopilot` menjalankan `run_autopilot` dari nol setiap kali job
dijalankan. Bila job mati di tengah (crash/timeout/stop) lalu di-retry, semua
cycle dan langkah yang sudah dicoba diulang -- biaya dan noise ke target
berlipat, dan langkah yang sebelumnya sudah terbukti negatif diuji ulang.

Kontrak: `run_autopilot(..., checkpoint=cb, resume=state, on_cycle=...)`
menyimpan progres per-cycle dan melanjutkan dari `cycles_done` + `attempted`
tanpa mengulang langkah yang sudah dicoba.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.hunt.autopilot import AutopilotStep, run_autopilot
from wth_ai.jobs.checkpoint import CheckpointEnvelope, StepOutcome, StepRecord


class _Resp:
    def __init__(self, status=200, body="ok"):
        self.status_code = status
        self.stdout = body
        self.transcript_id = f"TX-{status}"
        self.response_headers = {}


def _counter():
    calls = []

    def probe(probe):
        calls.append(probe.get("url"))
        return _Resp()

    return calls, probe


def test_checkpoint_saved_per_cycle():
    calls, probe = _counter()
    saved: list[AutopilotCheckpoint] = []

    def checkpoint(cp: AutopilotCheckpoint) -> None:
        saved.append(cp)

    run_autopilot(
        target="https://example.com",
        run_probe=probe,
        max_cycles=3,
        max_steps_per_cycle=2,
        checkpoint=checkpoint,
        payload_ids=["IN-001", "IN-002", "IN-003", "IN-004"],
    )
    assert saved, "checkpoint harus dipanggil"
    env = saved[-1]
    assert env.schema_version >= 2
    assert env.cursor >= 1
    assert env.steps


def test_resume_skips_already_attempted_steps():
    calls_a, probe_a = _counter()
    saved: list[AutopilotCheckpoint] = []
    run_autopilot(
        target="https://example.com", run_probe=probe_a,
        max_cycles=2, max_steps_per_cycle=2,
        checkpoint=lambda cp: saved.append(cp), payload_ids=["IN-001", "IN-002", "IN-003", "IN-004"],
    )
    assert calls_a
    completed = saved[-1]

    calls_b, probe_b = _counter()
    run_autopilot(
        target="https://example.com", run_probe=probe_b,
        max_cycles=2, max_steps_per_cycle=2,
        resume=completed,
        payload_ids=["IN-001", "IN-002", "IN-003", "IN-004"],
    )
    # Resume must not re-attempt anything already recorded as attempted.
    overlap = set(calls_a) & set(calls_b)
    assert not overlap, f"resume mengulang langkah: {overlap}"


def test_checkpoint_roundtrip_serializes():
    env = CheckpointEnvelope(
        engine="autopilot", target="https://example.com", cursor=2,
        steps=[
            StepRecord(key="GET https://example.com/?a=1", outcome=StepOutcome.COMPLETED, verified=True),
        ],
    )
    data = env.to_dict()
    restored = CheckpointEnvelope.from_dict(data)
    assert restored.cursor == 2
    assert restored.should_skip("GET https://example.com/?a=1")
    assert restored.step("GET https://example.com/?a=1").outcome == StepOutcome.COMPLETED


def test_checkpoint_failure_does_not_break_run():
    calls, probe = _counter()

    def boom(_cp):
        raise OSError("disk full")

    report = run_autopilot(
        target="https://example.com", run_probe=probe,
        max_cycles=1, max_steps_per_cycle=1,
        checkpoint=boom, vuln_class="xss",
    )
    assert report.cycles >= 1  # run tetap selesai, checkpoint best-effort
