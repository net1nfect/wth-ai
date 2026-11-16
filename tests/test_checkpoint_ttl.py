"""Kebijakan umur checkpoint (rekomendasi 3).

Sebelum ini tidak ada TTL sama sekali: checkpoint dari minggu lalu dipakai
seolah masih mencerminkan target hari ini, sehingga langkah `COMPLETED` yang
sebenarnya sudah basi dilewati. Kebijakan dibuat EKSPLISIT di sini:

* default (0/None) = tidak ada batas umur, perilaku lama dipertahankan;
* checkpoint tanpa `saved_at` TIDAK pernah dianggap basi (umur tak diketahui
  bukan alasan membuang state);
* resume yang ditolak karena umur melaporkan alasan yang bisa dibaca, dan
  tidak menandai langkah lama sebagai selesai.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.hunt.autopilot import run_autopilot
from wth_ai.jobs.checkpoint import CheckpointEnvelope, StepOutcome, make_step_key

TARGET = "https://example.com"
CART = "https://example.com/api/cart"


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status
        self.stdout = "ok"
        self.transcript_id = f"TX-{status}"
        self.response_headers = {}


def _run(**kw):
    base = dict(target=TARGET, max_cycles=1, max_steps_per_cycle=1, adaptive=False)
    base.update(kw)
    return run_autopilot(**base)


# ---------------------------------------------------------------------------
# unit: age_seconds / is_stale
# ---------------------------------------------------------------------------


def test_age_seconds_none_when_unknown():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET)
    assert env.age_seconds() is None
    assert env.is_stale(3600) is False, "umur tidak diketahui bukan basi"


def test_age_seconds_computes_from_saved_at():
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, saved_at=stamp)
    age = env.age_seconds()
    assert age is not None and 110 <= age <= 130


def test_zero_or_negative_max_age_disables_policy():
    stamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, saved_at=stamp)
    assert env.is_stale(0) is False
    assert env.is_stale(-5) is False


def test_stale_when_older_than_policy():
    stamp = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, saved_at=stamp)
    assert env.is_stale(3600) is True
    assert env.is_stale(3600 * 10) is False


def test_malformed_saved_at_is_not_stale():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, saved_at="bukan-tanggal")
    assert env.age_seconds() is None
    assert env.is_stale(1) is False


def test_saved_at_survives_roundtrip():
    stamp = datetime.now(timezone.utc).isoformat()
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, saved_at=stamp)
    assert CheckpointEnvelope.from_dict(env.to_dict()).saved_at == stamp


# ---------------------------------------------------------------------------
# integrasi: resume
# ---------------------------------------------------------------------------


def _completed_env(age_seconds: float) -> CheckpointEnvelope:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=1, saved_at=stamp)
    env.mark(make_step_key("POST", CART), StepOutcome.COMPLETED, verified=True,
             operation_id="pid:BL-001")
    return env


def test_stale_checkpoint_is_rejected_with_reason():
    env = _completed_env(age_seconds=7200)
    ran: list[dict] = []

    def probe(p: dict) -> _Resp:
        ran.append(dict(p))
        return _Resp()

    report = _run(
        run_probe=probe, resume=env, destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__",
        checkpoint_max_age_seconds=60,
    )
    assert "terlalu tua" in report.resume_rejected_reason
    # Langkah lama TIDAK boleh dilewati sebagai selesai.
    assert not report.checkpoint.should_skip_identity("pid:BL-001") if report.checkpoint else True


def test_fresh_checkpoint_is_used_normally():
    env = _completed_env(age_seconds=10)
    ran: list[dict] = []

    def probe(p: dict) -> _Resp:
        if p.get("mutating") is False:
            return _Resp()
        ran.append(dict(p))
        return _Resp()

    report = _run(
        run_probe=probe, resume=env, destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__",
        checkpoint_max_age_seconds=3600,
    )
    assert report.resume_rejected_reason == ""
    assert ran == [], "langkah COMPLETED yang masih segar harus dilewati"


def test_no_policy_keeps_old_behaviour():
    """Tanpa kebijakan, checkpoint tua tetap dipakai (kompatibilitas)."""
    env = _completed_env(age_seconds=86400 * 30)
    report = _run(run_probe=lambda p: _Resp(), resume=env,
                  destructive_allowed=True, payload_ids=["BL-001"],
                  vuln_class="__none__")
    assert report.resume_rejected_reason == ""


def test_checkpoint_without_saved_at_is_never_rejected_by_ttl():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=1)
    report = _run(run_probe=lambda p: _Resp(), resume=env,
                  destructive_allowed=True, payload_ids=["BL-001"],
                  vuln_class="__none__", checkpoint_max_age_seconds=1)
    assert "terlalu tua" not in report.resume_rejected_reason


def test_checkpoint_written_by_run_has_saved_at():
    report = _run(run_probe=lambda p: _Resp(), destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__")
    assert report.checkpoint is not None
    assert report.checkpoint.saved_at, "checkpoint baru wajib punya saved_at"
    assert report.checkpoint.age_seconds() is not None


# ---------------------------------------------------------------------------
# peringatan kebijakan: destruktif tanpa TTL
# ---------------------------------------------------------------------------


def test_destructive_without_ttl_reports_policy_warning():
    report = _run(run_probe=lambda p: _Resp(), destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__")
    assert "checkpoint_max_age_seconds" in report.checkpoint_policy_warning
    assert report.to_dict()["checkpoint_policy_warning"] == (
        report.checkpoint_policy_warning
    )


def test_destructive_with_ttl_has_no_policy_warning():
    report = _run(run_probe=lambda p: _Resp(), destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__",
                  checkpoint_max_age_seconds=3600)
    assert report.checkpoint_policy_warning == ""


def test_non_destructive_without_ttl_has_no_policy_warning():
    report = _run(run_probe=lambda p: _Resp(), destructive_allowed=False,
                  payload_ids=["IN-001"], vuln_class="__none__")
    assert report.checkpoint_policy_warning == ""
    assert report.to_dict()["checkpoint_policy_warning"] == ""


def test_negative_ttl_warns_because_it_disables_the_limit():
    report = _run(run_probe=lambda p: _Resp(), destructive_allowed=True,
                  payload_ids=["BL-001"], vuln_class="__none__",
                  checkpoint_max_age_seconds=-5)
    assert report.checkpoint_policy_warning, (
        "TTL negatif berarti tanpa batas; engagement destruktif harus diperingatkan"
    )
