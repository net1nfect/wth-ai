"""Regression tests — checkpoint schema umum (engine-agnostic).

Failure mode yang dibuktikan sebelum perubahan:
* checkpoint tidak punya `schema_version` -> perubahan struktur tidak
  terdeteksi;
* `cycles_done` negatif diterima apa adanya;
* `attempted` hanya (method,url) -> tidak bisa membedakan step yang benar-benar
  SELESAI dari yang hanya "sempat dicoba" (gagal/terputus);
* `from_dict(list)` melempar `AttributeError` mentah;
* resume menganggap step `ran=False` sebagai selesai.

Kontrak baru: `CheckpointEnvelope` dengan versi skema, validasi tipe, target,
cursor non-negatif, dan klasifikasi per-step (completed/failed/inflight/pending).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.jobs.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEnvelope,
    CheckpointValidationError,
    StepOutcome,
    StepRecord,
)


def _envelope(**kw) -> CheckpointEnvelope:
    base = dict(
        engine="autopilot",
        target="https://example.com",
        cursor=2,
        steps=[
            StepRecord(key="GET https://example.com/a", outcome=StepOutcome.COMPLETED, verified=True),
            StepRecord(key="GET https://example.com/b", outcome=StepOutcome.FAILED, note="timeout"),
            StepRecord(key="GET https://example.com/c", outcome=StepOutcome.INFLIGHT),
            StepRecord(key="GET https://example.com/d", outcome=StepOutcome.PENDING),
        ],
    )
    base.update(kw)
    return CheckpointEnvelope(**base)


# -- versi & serialisasi ----------------------------------------------------


def test_schema_version_is_persisted():
    data = _envelope().to_dict()
    assert data["schema_version"] == CHECKPOINT_SCHEMA_VERSION


def test_roundtrip_preserves_everything():
    env = _envelope()
    restored = CheckpointEnvelope.from_dict(env.to_dict())
    assert restored == env


def test_unknown_future_version_is_rejected():
    data = _envelope().to_dict()
    data["schema_version"] = CHECKPOINT_SCHEMA_VERSION + 1
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


def test_missing_version_is_rejected():
    data = _envelope().to_dict()
    data.pop("schema_version")
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


def test_old_flat_autopilot_checkpoint_is_migrated():
    """Checkpoint v1 (tanpa versi, `attempted` saja) harus tetap terbaca.

    Migrasi konservatif: attempted TIDAK otomatis dianggap completed --
    outcome-nya UNKNOWN, sehingga resume boleh memutuskan untuk mengulang.
    """
    legacy = {
        "target": "https://example.com",
        "cycles_done": 2,
        "attempted": [["GET", "https://example.com/a"]],
        "steps": [],
    }
    env = CheckpointEnvelope.from_legacy(legacy, engine="autopilot")
    assert env.schema_version == CHECKPOINT_SCHEMA_VERSION
    assert env.cursor == 2
    assert env.step("GET https://example.com/a").outcome == StepOutcome.UNKNOWN
    assert not env.step("GET https://example.com/a").verified


# -- validasi ---------------------------------------------------------------


@pytest.mark.parametrize("bad", [-1, -100])
def test_negative_cursor_rejected(bad):
    with pytest.raises(CheckpointValidationError):
        _envelope(cursor=bad)


def test_non_int_cursor_rejected():
    with pytest.raises(CheckpointValidationError):
        _envelope(cursor="2")


def test_empty_target_rejected():
    with pytest.raises(CheckpointValidationError):
        _envelope(target="")


def test_unknown_engine_rejected():
    with pytest.raises(CheckpointValidationError):
        _envelope(engine="totally-unknown-engine")


def test_from_dict_on_non_mapping_raises_validation_error():
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict([])  # type: ignore[arg-type]


def test_from_dict_rejects_bad_step_shape():
    data = _envelope().to_dict()
    data["steps"] = [{"outcome": "completed"}]  # no key
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


# -- klasifikasi status -----------------------------------------------------


def test_completed_requires_verification_to_be_skippable():
    env = _envelope()
    assert env.should_skip("GET https://example.com/a") is True
    unverified = CheckpointEnvelope(
        engine="autopilot", target="https://example.com",
        steps=[StepRecord(key="X", outcome=StepOutcome.COMPLETED, verified=False)],
    )
    assert unverified.should_skip("X") is False


def test_failed_step_is_retryable():
    env = _envelope()
    assert env.should_skip("GET https://example.com/b") is False
    assert env.retryable("GET https://example.com/b") is True


def test_inflight_step_is_not_treated_as_completed():
    env = _envelope()
    assert env.should_skip("GET https://example.com/c") is False
    assert env.step("GET https://example.com/c").outcome == StepOutcome.INFLIGHT


def test_pending_and_unknown_not_skipped():
    env = _envelope()
    assert env.should_skip("GET https://example.com/d") is False
    assert env.should_skip("never-seen") is False


def test_outcomes_partition_is_complete():
    env = _envelope()
    assert env.completed_keys() == {"GET https://example.com/a"}
    assert env.failed_keys() == {"GET https://example.com/b"}
    assert env.inflight_keys() == {"GET https://example.com/c"}


# -- target & idempotensi ---------------------------------------------------


def test_target_mismatch_detected():
    env = _envelope(target="https://example.com")
    assert env.matches_target("https://example.com")
    assert not env.matches_target("https://other.tld")


def test_upsert_is_idempotent():
    env = _envelope()
    env.mark("NEW", StepOutcome.COMPLETED, verified=True)
    env.mark("NEW", StepOutcome.COMPLETED, verified=True)
    assert [s.key for s in env.steps].count("NEW") == 1


def test_mark_transitions_outcome():
    env = _envelope()
    env.mark("GET https://example.com/b", StepOutcome.INFLIGHT)
    assert env.step("GET https://example.com/b").outcome == StepOutcome.INFLIGHT
    env.mark("GET https://example.com/b", StepOutcome.COMPLETED, verified=True)
    assert env.should_skip("GET https://example.com/b")


# -- adapter per engine -----------------------------------------------------


def test_engine_without_adapter_is_rejected_not_silently_accepted():
    """Worker/recon/executor belum punya adapter; checkpoint mereka tidak
    boleh diterima seolah-olah formatnya sudah dipahami."""
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope(engine="worker", target="https://example.com")


def test_autopilot_payload_is_validated_on_write():
    env = _envelope()
    env.engine_state["steps"] = "not-a-list"
    with pytest.raises(CheckpointValidationError):
        env.to_dict()


def test_autopilot_payload_is_validated_on_read():
    data = _envelope().to_dict()
    data["engine_state"] = {"steps": ["not-a-mapping"]}
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


def test_set_engine_state_validates_and_is_atomic():
    env = _envelope()
    before = dict(env.engine_state)
    with pytest.raises(CheckpointValidationError):
        env.set_engine_state({"steps": 123})
    assert env.engine_state == before, "state lama tidak boleh rusak saat validasi gagal"


def test_bad_status_type_is_validation_error_not_raw_value_error():
    data = _envelope().to_dict()
    data["steps"][0]["status"] = "not-a-number"
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


def test_non_string_target_is_rejected():
    data = _envelope().to_dict()
    data["target"] = {"nested": "obj"}
    with pytest.raises(CheckpointValidationError):
        CheckpointEnvelope.from_dict(data)


def test_resource_budget_roundtrip():
    env = _envelope(resource_budget={"max_cycles": 3, "max_steps_per_cycle": 0})
    restored = CheckpointEnvelope.from_dict(env.to_dict())
    assert restored.resource_budget == {"max_cycles": 3, "max_steps_per_cycle": 0}


@pytest.mark.parametrize("bad", [{"max_cycles": -1}, {"max_cycles": True}, {"x": "3"}])
def test_resource_budget_rejects_invalid_values(bad):
    with pytest.raises(CheckpointValidationError):
        _envelope(resource_budget=bad)
