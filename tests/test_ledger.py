"""Test hash-chain quality ledger (fitur Strix yang dibawa ke WTH-AI)."""

from __future__ import annotations

import sqlite3

import pytest

from wth_ai.redteam.ledger import IdempotencyConflictError, Ledger, sha256_bytes


def _make_ledger(tmp_path):
    return Ledger(tmp_path / "ledger.sqlite")


def test_wal_mode_forced(tmp_path):
    ledger = _make_ledger(tmp_path)
    con = sqlite3.connect(str(tmp_path / "ledger.sqlite"))
    mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()
    assert mode == "wal"
    con.close()


def test_append_returns_event_and_revision(tmp_path):
    ledger = _make_ledger(tmp_path)
    ev = ledger.append(
        "finding.filed",
        idempotency_key="k1",
        object_type="finding",
        object_id="FIND-01",
        actor_id="executor",
        actor_role="discovery",
        payload={"state": "candidate"},
    )
    assert ev["sequence"] == 1
    assert ev["ledger_revision"] == 1
    assert ev["previous_event_hash"] == "0" * 64
    assert len(ev["hash"]) == 64


def test_chain_links_events(tmp_path):
    ledger = _make_ledger(tmp_path)
    a = ledger.append("a", idempotency_key="a")
    b = ledger.append("b", idempotency_key="b")
    assert b["previous_event_hash"] == a["hash"]
    assert b["sequence"] == a["sequence"] + 1


def test_idempotency_replay_returns_stored(tmp_path):
    ledger = _make_ledger(tmp_path)
    first = ledger.append("finding.reproduced", idempotency_key="dup", payload={"n": 1})
    second = ledger.append("finding.reproduced", idempotency_key="dup", payload={"n": 1})
    assert second == first
    # Total events = 1, bukan 2.
    assert ledger.count_events() == 1


def test_idempotency_conflict_different_payload(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.append("finding.reproduced", idempotency_key="dup", payload={"n": 1})
    with pytest.raises(IdempotencyConflictError):
        ledger.append("finding.reproduced", idempotency_key="dup", payload={"n": 2})


def test_verify_chain_ok(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.append("a", idempotency_key="a")
    ledger.append("b", idempotency_key="b")
    ledger.append("c", idempotency_key="c")
    assert ledger.verify_chain() is True


def test_verify_chain_detects_tamper(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.append("a", idempotency_key="a")
    ledger.append("b", idempotency_key="b")
    # Mutasi byte: ubah payload event pertama langsung di SQLite.
    con = sqlite3.connect(str(tmp_path / "ledger.sqlite"))
    con.execute("UPDATE events SET request_json = ? WHERE sequence = 1", ('{"tampered":true}',))
    con.commit()
    con.close()
    assert ledger.verify_chain() is False


def test_verify_chain_detects_missing_middle(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.append("a", idempotency_key="a")
    ledger.append("b", idempotency_key="b")
    ledger.append("c", idempotency_key="c")
    con = sqlite3.connect(str(tmp_path / "ledger.sqlite"))
    con.execute("DELETE FROM events WHERE sequence = 2")
    con.commit()
    con.close()
    assert ledger.verify_chain() is False


def test_snapshot_is_read_only(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.append("a", idempotency_key="a")
    snap = ledger.snapshot()
    # Query di snapshot boleh; write harus ditolak karena query_only.
    assert snap.count_events() == 1
    with pytest.raises(sqlite3.OperationalError):
        snap.append("w", idempotency_key="w")


def test_sha256_bytes_deterministic():
    assert sha256_bytes(b"x") == sha256_bytes(b"x")
    assert sha256_bytes(b"x") != sha256_bytes(b"y")
