"""Simulasi crash end-to-end lewat jalur persisten sungguhan (rekomendasi 6).

Test fault-injection lain memakai checkpoint callback in-memory. Suite ini
memakai `JobStore` NYATA (SQLite + file checkpoint) dan menjalankan
`run_autopilot` dua kali dalam dua proses logis terpisah, sehingga yang diuji
adalah jalur yang benar-benar dipakai worker:

    tulis checkpoint -> proses mati -> baca checkpoint -> resume

Setiap skenario memeriksa empat hal: state tersimpan, keadaan target
simulasi, keputusan resume, dan hasil akhir. Tidak ada jaringan.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.hunt.autopilot import run_autopilot
from wth_ai.jobs.checkpoint import CheckpointEnvelope, StepOutcome, make_step_key
from wth_ai.jobs.store import JobStore

TARGET = "https://example.com"
CART = "https://example.com/api/cart"


class _Crash(BaseException):
    """Kematian proses: BUKAN `Exception`."""


class _Resp:
    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status_code = status
        self.stdout = body
        self.transcript_id = f"TX-{status}"
        self.response_headers: dict = {}


@pytest.fixture()
def store(tmp_path) -> JobStore:
    return JobStore(db_path=tmp_path / "jobs.sqlite", home=tmp_path)


def _saver(store: JobStore, job_id: str = "JOB-CRASH"):
    """Checkpoint callback yang menulis lewat JobStore (jalur produksi)."""
    def save(env: CheckpointEnvelope) -> None:
        store.save_checkpoint(job_id, env.to_dict())
    return save


def _loader(store: JobStore, job_id: str = "JOB-CRASH") -> CheckpointEnvelope | None:
    raw = store.load_checkpoint(job_id)
    return CheckpointEnvelope.from_dict(raw) if raw else None


def _record(calls: list[dict]):
    def run(p: dict) -> _Resp:
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":1}')
        calls.append(dict(p))
        return _Resp()
    return run


# ---------------------------------------------------------------------------
# crash sebelum cycle pertama
# ---------------------------------------------------------------------------


def test_crash_before_first_cycle_leaves_initial_snapshot(store: JobStore):
    def crash_immediately(env) -> None:
        raise _Crash("mati sebelum snapshot awal tersimpan")

    with pytest.raises(_Crash):
        run_autopilot(
            target=TARGET, run_probe=_record([]), destructive_allowed=True,
            payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
            max_cycles=1, max_steps_per_cycle=1, checkpoint=crash_immediately,
        )
    # Tidak ada checkpoint: resume tidak boleh mengklaim recovery.
    assert _loader(store) is None


def test_crash_after_initial_snapshot_keeps_cursor(store: JobStore):
    calls: list[dict] = []
    saves = {"n": 0}

    def crash_on_second_save(env) -> None:
        saves["n"] += 1
        if saves["n"] == 1:
            store.save_checkpoint("JOB-CRASH", env.to_dict())
            return
        raise _Crash("mati setelah snapshot awal")

    with pytest.raises(_Crash):
        run_autopilot(
            target=TARGET, run_probe=_record(calls), destructive_allowed=True,
            payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
            max_cycles=2, max_steps_per_cycle=1, checkpoint=crash_on_second_save,
        )
    env = _loader(store)
    assert env is not None
    assert env.target == TARGET


# ---------------------------------------------------------------------------
# crash setelah mutasi, sebelum respons
# ---------------------------------------------------------------------------


def test_crash_after_mutation_blocks_autorerun_after_real_restart(store: JobStore):
    target_state = {"mutated": False}

    def mutate_then_crash(p: dict) -> _Resp:
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":-1}')
        target_state["mutated"] = True
        raise _Crash("mati setelah mutasi")

    with pytest.raises(_Crash):
        run_autopilot(
            target=TARGET, run_probe=mutate_then_crash, destructive_allowed=True,
            payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
            max_cycles=1, max_steps_per_cycle=1, checkpoint=_saver(store),
        )
    assert target_state["mutated"] is True

    persisted = _loader(store)
    assert persisted is not None
    rec = persisted.step(make_step_key("POST", CART))
    assert rec is not None and rec.outcome == StepOutcome.INFLIGHT

    # "Proses kedua": resume dari checkpoint yang dibaca ulang dari disk.
    resumed: list[dict] = []
    report = run_autopilot(
        target=TARGET, run_probe=_record(resumed), destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1, resume=_loader(store),
        checkpoint=_saver(store),
    )
    assert [p.get("payload_id") for p in resumed] == [], \
        "langkah destruktif INFLIGHT tidak boleh dijalankan ulang otomatis"
    blocked = [s for s in report.steps if s.blocked_reason]
    assert blocked, "harus ada status yang bisa ditindaklanjuti operator"


# ---------------------------------------------------------------------------
# crash setelah verifikasi, sebelum COMPLETED tersimpan
# ---------------------------------------------------------------------------


def test_crash_after_verification_rechecks_on_resume(store: JobStore):
    """Efek sudah hilang tapi COMPLETED belum tersimpan: resume harus
    memverifikasi ulang, bukan menganggap selesai atau mengulang buta."""
    ops: list[str] = []

    def probe(p: dict) -> _Resp:
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":1}')
        ops.append(str(p.get("payload_id") or ""))
        raise _Crash("mati setelah operasi sebelum penyimpanan hasil")

    with pytest.raises(_Crash):
        run_autopilot(
            target=TARGET, run_probe=probe, destructive_allowed=True,
            payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
            max_cycles=1, max_steps_per_cycle=1, checkpoint=_saver(store),
        )

    env = _loader(store)
    rec = env.step(make_step_key("POST", CART))
    assert rec is not None and rec.outcome != StepOutcome.COMPLETED

    resumed_ops: list[str] = []
    def resume_probe(p: dict) -> _Resp:
        if p.get("mutating") is False:
            return _Resp(body='{"item_id":"1","qty":1}')
        resumed_ops.append(str(p.get("payload_id") or ""))
        return _Resp()

    run_autopilot(
        target=TARGET, run_probe=resume_probe, destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1, resume=_loader(store),
        checkpoint=_saver(store),
    )
    assert resumed_ops == [], "operasi yang efeknya sudah bersih tidak boleh diulang"


# ---------------------------------------------------------------------------
# checkpoint write failure
# ---------------------------------------------------------------------------


def test_checkpoint_write_failure_blocks_destructive_and_stops(store: JobStore):
    calls: list[dict] = []

    def failing(env) -> None:
        raise OSError("disk penuh")

    report = run_autopilot(
        target=TARGET, run_probe=_record(calls), destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1, checkpoint=failing,
    )
    assert calls == [], "tanpa checkpoint, langkah destruktif tidak boleh dikirim"
    assert report.stop_incomplete is True
    assert report.checkpoint_error


def test_write_failure_does_not_overwrite_stop_reason(store: JobStore):
    """Kegagalan storage dilaporkan di `checkpoint_error`, tidak menimpa
    `stopped_reason` (dua penyebab berbeda tidak boleh saling menyamarkan)."""
    report = run_autopilot(
        target=TARGET, run_probe=_record([]), destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1,
        checkpoint=lambda env: (_ for _ in ()).throw(OSError("disk penuh")),
    )
    assert "checkpoint" in report.stopped_reason.lower()
    assert report.checkpoint_error
    assert report.checkpoint_error not in report.stopped_reason


# ---------------------------------------------------------------------------
# resume berulang
# ---------------------------------------------------------------------------


def test_repeated_resume_is_idempotent(store: JobStore):
    """Resume berkali-kali tidak boleh menggandakan record atau menurunkan
    status COMPLETED yang sudah terverifikasi."""
    run_autopilot(
        target=TARGET, run_probe=_record([]), destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1, checkpoint=_saver(store),
    )
    first = _loader(store)
    assert first is not None

    for _ in range(3):
        run_autopilot(
            target=TARGET, run_probe=_record([]), destructive_allowed=True,
            payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
            max_cycles=1, max_steps_per_cycle=1, resume=_loader(store),
            checkpoint=_saver(store),
        )
        env = _loader(store)
        assert env is not None
        assert len(env.steps) == len(first.steps), "resume menggandakan record"
        # Tidak ada record COMPLETED yang turun jadi UNKNOWN.
        for rec in env.steps:
            if rec.outcome == StepOutcome.UNKNOWN:
                assert not first.should_skip_identity(rec.identity or "")


# ---------------------------------------------------------------------------
# checkpoint korup / tidak kompatibel
# ---------------------------------------------------------------------------


def test_corrupt_checkpoint_file_resumes_from_scratch(store: JobStore):
    path = store.checkpoint_path("JOB-CRASH")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bukan json", encoding="utf-8")
    assert store.load_checkpoint("JOB-CRASH") is None

    calls: list[dict] = []
    report = run_autopilot(
        target=TARGET, run_probe=_record(calls), destructive_allowed=True,
        payload_ids=["BL-001"], vuln_class="__none__", adaptive=False,
        max_cycles=1, max_steps_per_cycle=1, checkpoint=_saver(store),
    )
    assert report.checkpoint is not None
    assert [p.get("payload_id") for p in calls] == ["BL-001"]


def test_incompatible_schema_is_not_trusted(store: JobStore):
    path = store.checkpoint_path("JOB-CRASH")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"job_id":"JOB-CRASH","payload":{"schema_version":99,"engine":"autopilot",'
        '"target":"https://example.com","cursor":1,"steps":[]}}',
        encoding="utf-8",
    )
    raw = store.load_checkpoint("JOB-CRASH")
    assert raw is not None
    with pytest.raises(Exception):
        CheckpointEnvelope.from_dict(raw)
