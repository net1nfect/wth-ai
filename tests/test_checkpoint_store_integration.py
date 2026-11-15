"""Regression — integrasi checkpoint store + handler autopilot (recovery).

Verifikasi yang dituntut: checkpoint ditulis, dibaca ulang sebagai envelope
v2, checkpoint lama v1 dimigrasi, dan checkpoint rusak tidak menyebabkan
eksekusi tak terkendali.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from pathlib import Path

from wth_ai.hunt.autopilot import AutopilotCheckpoint
from wth_ai.jobs.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointEnvelope,
    CheckpointValidationError,
    StepOutcome,
)
from wth_ai.jobs.store import JobStore


def _store(tmp_path: Path) -> JobStore:
    return JobStore(home=tmp_path)


def test_store_roundtrip_envelope(tmp_path):
    store = _store(tmp_path)
    env = CheckpointEnvelope(engine="autopilot", target="https://example.com", cursor=2)
    env.mark_http("GET", "https://example.com/a", StepOutcome.COMPLETED, verified=True)
    store.save_checkpoint("JOB-abc", env.to_dict())

    loaded = store.load_checkpoint("JOB-abc")
    assert loaded is not None
    assert loaded["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    restored = AutopilotCheckpoint.from_dict(loaded)
    assert restored.should_skip("GET https://example.com/a")


def test_legacy_v1_checkpoint_is_migrated_not_trusted(tmp_path):
    store = _store(tmp_path)
    store.save_checkpoint("JOB-old", {
        "target": "https://example.com",
        "cycles_done": 1,
        "attempted": [["GET", "https://example.com/a"]],
        "steps": [],
    })
    env = AutopilotCheckpoint.from_dict(store.load_checkpoint("JOB-old"))
    assert env.schema_version == CHECKPOINT_SCHEMA_VERSION
    assert env.cursor == 1
    # legacy attempted must NOT be skippable
    assert env.should_skip("GET https://example.com/a") is False


def test_corrupt_checkpoint_file_returns_none(tmp_path):
    store = _store(tmp_path)
    path = store.checkpoint_path("JOB-bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert store.load_checkpoint("JOB-bad") is None


def test_checkpoint_with_wrong_payload_type_returns_none(tmp_path):
    store = _store(tmp_path)
    path = store.checkpoint_path("JOB-wrong")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"job_id": "JOB-wrong", "payload": [1, 2]}), encoding="utf-8")
    assert store.load_checkpoint("JOB-wrong") is None


def test_from_dict_rejects_non_mapping():
    import pytest

    with pytest.raises(CheckpointValidationError):
        AutopilotCheckpoint.from_dict([1, 2])  # type: ignore[arg-type]


def test_checkpoint_path_is_sanitized(tmp_path):
    store = _store(tmp_path)
    path = store.checkpoint_path("../../etc/passwd")
    assert ".." not in str(path.name)
    assert path.parent == store.checkpoint_dir()
