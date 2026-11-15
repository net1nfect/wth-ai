"""Checkpoint CAS/lease (rekomendasi 2).

Defek yang dikunci di sini: dua writer pada job yang sama. Dengan
`os.replace` biasa, writer yang lebih lambat menimpa checkpoint writer yang
lebih cepat, dan langkah yang sudah ditemukan proses lain hilang tanpa jejak.
CAS harus MENOLAK snapshot basi, bukan menyimpannya dengan sopan.

Semua test memakai direktori sementara; tidak ada DB/jaringan nyata.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.jobs.checkpoint import CheckpointEnvelope, make_step_key
from wth_ai.jobs.store import JobStore

TARGET = "https://example.com"

@pytest.fixture()
def store(tmp_path: Path) -> JobStore:
    return JobStore(db_path=tmp_path / "jobs.sqlite", home=tmp_path)

def _env(cursor: int = 1, *, run_id: str = "", revision: int = 0) -> dict:
    env = CheckpointEnvelope(
        engine="autopilot", target=TARGET, cursor=cursor,
        run_id=run_id, revision=revision,
    )
    return env.to_dict()

# ---------------------------------------------------------------------------
# dasar
# ---------------------------------------------------------------------------

def test_first_writer_is_accepted(store: JobStore):
    path = store.save_checkpoint_cas("JOB-1", _env(1), run_id="A", revision=1)
    assert path is not None
    loaded = store.load_checkpoint("JOB-1")
    assert loaded is not None
    assert loaded["run_id"] == "A"
    assert loaded["revision"] == 1
    assert loaded["cursor"] == 1

def test_stale_revision_is_rejected_not_merged(store: JobStore):
    """Writer dengan revision lebih kecil TIDAK boleh menimpa."""
    store.save_checkpoint_cas("JOB-1", _env(5), run_id="A", revision=5)
    result = store.save_checkpoint_cas("JOB-1", _env(1), run_id="A", revision=3)
    assert result is None
    loaded = store.load_checkpoint("JOB-1")
    assert loaded["cursor"] == 5
    assert loaded["revision"] == 5

def test_same_owner_can_advance(store: JobStore):
    store.save_checkpoint_cas("JOB-1", _env(1), run_id="A", revision=1)
    assert store.save_checkpoint_cas("JOB-1", _env(2), run_id="A", revision=2) is not None
    loaded = store.load_checkpoint("JOB-1")
    assert loaded["cursor"] == 2 and loaded["revision"] == 2

def test_equal_revision_is_rejected(store: JobStore):
    """Revision yang sama = belum ada progres baru; jangan tulis ulang."""
    store.save_checkpoint_cas("JOB-1", _env(1), run_id="A", revision=4)
    assert store.save_checkpoint_cas("JOB-1", _env(9), run_id="A", revision=4) is None
    assert store.load_checkpoint("JOB-1")["cursor"] == 1

# ---------------------------------------------------------------------------
# skenario dua writer
# ---------------------------------------------------------------------------

def test_late_worker_cannot_clobber_fresh_checkpoint(store: JobStore):
    """Worker A macet, worker B mengambil alih dan menemukan langkah baru,
    lalu A bangun dan menulis snapshot basi. Snapshot A harus ditolak."""
    # B mengambil alih dengan revision lebih tinggi.
    store.save_checkpoint_cas("JOB-1", _env(10), run_id="B-run", revision=10)
    found = store.load_checkpoint("JOB-1")

    # A menulis snapshot lama (cursor kecil, revision kecil).
    stale = store.save_checkpoint_cas("JOB-1", _env(2), run_id="A-run", revision=4)
    assert stale is None, "snapshot basi tidak boleh ditulis"

    after = store.load_checkpoint("JOB-1")
    assert after["cursor"] == found["cursor"] == 10
    assert after["run_id"] == "B-run"

def test_new_owner_can_take_over_with_higher_revision(store: JobStore):
    """Ambil alih sah: claim baru HARUS punya revision lebih tinggi."""
    store.save_checkpoint_cas("JOB-1", _env(3), run_id="old", revision=3)
    assert store.save_checkpoint_cas("JOB-1", _env(4), run_id="new", revision=4) is not None
    loaded = store.load_checkpoint("JOB-1")
    assert loaded["run_id"] == "new" and loaded["cursor"] == 4

# ---------------------------------------------------------------------------
# kompatibilitas checkpoint lama
# ---------------------------------------------------------------------------

def test_legacy_checkpoint_without_run_id_does_not_block_writer(store: JobStore):
    """Checkpoint lama (tanpa run_id/revision) tidak boleh mengunci job
    selamanya: revision 0 berarti 'tidak diketahui', bukan 'terbaru'."""
    legacy = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=2).to_dict()
    legacy.pop("run_id", None)
    legacy.pop("revision", None)
    store.save_checkpoint("JOB-1", legacy)

    assert store.save_checkpoint_cas("JOB-1", _env(3), run_id="A", revision=1) is not None
    loaded = store.load_checkpoint("JOB-1")
    assert loaded["cursor"] == 3 and loaded["run_id"] == "A"

def test_legacy_read_still_works_after_cas_write(store: JobStore):
    store.save_checkpoint_cas("JOB-1", _env(1), run_id="A", revision=1)
    env = CheckpointEnvelope.from_dict(store.load_checkpoint("JOB-1"))
    assert env.run_id == "A" and env.revision == 1

# ---------------------------------------------------------------------------
# validasi payload
# ---------------------------------------------------------------------------

def test_invalid_payload_is_never_written(store: JobStore):
    """Payload yang tidak lolos validasi envelope tidak boleh sampai ke disk."""
    bad = _env(1)
    bad["engine"] = "engine-yang-tidak-ada"
    with pytest.raises(Exception):
        store.save_checkpoint_cas("JOB-1", bad, run_id="A", revision=1)
    assert store.load_checkpoint("JOB-1") is None

def test_run_id_and_revision_roundtrip_through_envelope():
    env = CheckpointEnvelope(engine="autopilot", target=TARGET, cursor=3,
                             run_id="R-42", revision=7)
    again = CheckpointEnvelope.from_dict(env.to_dict())
    assert (again.run_id, again.revision) == ("R-42", 7)

def test_negative_revision_is_rejected():
    with pytest.raises(Exception):
        CheckpointEnvelope(engine="autopilot", target=TARGET, revision=-1)

def test_bool_revision_is_rejected():
    with pytest.raises(Exception):
        CheckpointEnvelope(engine="autopilot", target=TARGET, revision=True)
