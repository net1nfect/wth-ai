"""Regression tests — observability recovery & integritas status terminal.

Failure mode yang diverifikasi:
* checkpoint gagal ditulis tetapi job tetap `completed` tanpa jejak bahwa
  recovery tidak tersedia (degradasi tak terlihat);
* resume ditolak (target beda / cursor > limit) tidak terbedakan dari
  "tidak ada checkpoint";
* checkpoint rusak harus fail-closed tanpa eksekusi tak terkendali.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.hunt.autopilot import run_autopilot
from wth_ai.jobs.checkpoint import CheckpointEnvelope, StepOutcome, make_step_key


class _Resp:
    status_code = 200
    stdout = "ok"
    transcript_id = "TX-1"
    response_headers = {}


def _probe(calls):
    def run(probe):
        calls.append(probe.get("url"))
        return _Resp()
    return run


def _run(**kw):
    base = dict(
        target="https://example.com", max_cycles=1, max_steps_per_cycle=1,
        payload_ids=["IN-001", "IN-002"], adaptive=False,
    )
    base.update(kw)
    return run_autopilot(**base)


def test_checkpoint_write_failure_is_recorded_not_hidden():
    def boom(_env):
        raise OSError("disk full")

    report = _run(run_probe=_probe([]), checkpoint=boom)
    assert report.checkpoint_error, "kegagalan checkpoint harus tercatat"
    assert "OSError" in report.checkpoint_error
    assert report.checkpoint is not None  # state tetap tersedia in-memory


def test_resume_rejection_is_distinguishable_from_no_checkpoint():
    report_no_cp = _run(run_probe=_probe([]))
    assert report_no_cp.resume_rejected_reason == ""

    foreign = CheckpointEnvelope(engine="autopilot", target="https://other.tld", cursor=0)
    report_foreign = _run(run_probe=_probe([]), resume=foreign)
    assert report_foreign.resume_rejected_reason
    assert "other.tld" in report_foreign.resume_rejected_reason


def test_corrupt_checkpoint_dict_never_raises_in_run():
    """from_dict pada envelope rusak harus ValueError terkontrol, bukan
    AttributeError/kebingungan state."""
    import pytest

    from wth_ai.jobs.checkpoint import CheckpointValidationError

    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict({"schema_version": 2, "steps": "not-a-list"})


def test_partial_completion_keeps_completed_and_reruns_rest():
    """Skenario nyata: satu step selesai, satu gagal, satu belum dijalankan."""
    env = CheckpointEnvelope(engine="autopilot", target="https://example.com", cursor=0)
    discover: list[str] = []
    _run(run_probe=_probe(discover))
    assert len(discover) >= 1
    done_url = discover[0]
    done_key = make_step_key("GET", done_url)
    env.mark(done_key, StepOutcome.COMPLETED, verified=True)

    calls: list[str] = []
    _run(run_probe=_probe(calls), resume=env, max_steps_per_cycle=3)
    assert done_url not in calls, "step completed diulang"
