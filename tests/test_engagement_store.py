"""Regression tests — WU-101: `EngagementStore` (SQLite), PRD-003, ADR-011.

Scope: store CRUD + `fetch()` fresh-read semantics ONLY. Wiring
`ExecutionBroker` untuk benar-benar memanggil `fetch()` sebelum setiap
eksekusi (lapis kedua enforcement, ADR-011) DITUNDA ke WU-104 -- lihat
scope decision di module docstring `engagement/store.py`. Command CLI
`/engagement create|authorize|...` (PRD-003 FR#2) juga belum dibangun.

`Engagement.from_dict()`/`to_dict()` round-trip (ditambahkan bersamaan WU
ini di `engagement/model.py`, net-new method, tanpa mengubah `__init__`
existing) diuji di sini secara tidak langsung lewat store, DAN langsung
lewat test unit terpisah untuk memastikan fidelity penuh termasuk status
FSM (bypass guard transisi, bukan re-run transition()).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from datetime import datetime, timedelta, timezone

from wth_ai.engagement.model import (
    AuthorizationSource,
    Engagement,
    EngagementStatus,
    TargetScope,
    TimeWindow,
)
from wth_ai.engagement.store import EngagementStore


def _store(tmp_path) -> EngagementStore:
    return EngagementStore(db_path=tmp_path / "engagements.sqlite")


def _draft(**kwargs) -> Engagement:
    return Engagement(name="test-eng", created_by="cli-user", **kwargs)


# ---------------------------------------------------------------------------
# Engagement.from_dict()/to_dict() -- round-trip fidelity (unit, tanpa store)
# ---------------------------------------------------------------------------

def test_to_dict_from_dict_roundtrip_preserves_active_status():
    """Bukti kunci: status ACTIVE (dicapai lewat transition() nyata) tetap
    ACTIVE setelah serialize+deserialize -- from_dict() TIDAK re-run FSM
    guard (kalau iya, ini akan gagal karena butuh approved_by dkk lagi)."""
    eng = _draft(targets=[TargetScope(pattern="example.com")])
    eng.transition("submit_for_validation", actor="operator:cli")
    eng.authorization_source = AuthorizationSource(
        kind="lab_disposable", reference="local", verified_by="operator:cli",
        verified_at=datetime.now(timezone.utc).isoformat(),
    )
    eng.transition("validation_passed", actor="operator:cli")
    eng.approved_by = "operator:cli"
    eng.transition("activate", actor="operator:cli")
    assert eng.status == EngagementStatus.ACTIVE

    restored = Engagement.from_dict(eng.to_dict())
    assert restored.status == EngagementStatus.ACTIVE
    assert restored.id == eng.id
    assert restored.approved_by == "operator:cli"
    assert restored.authorization_source is not None
    assert restored.authorization_source.kind == "lab_disposable"
    assert restored.targets[0].pattern == "example.com"


def test_roundtrip_still_computes_auto_expired_live():
    """restored.status HARUS tetap auto-compute EXPIRED dari time_window --
    bukan cuma nilai statis yang dibekukan saat serialize."""
    eng = _draft(targets=[TargetScope(pattern="example.com")])
    eng.transition("submit_for_validation", actor="operator:cli")
    eng.authorization_source = AuthorizationSource(
        kind="lab_disposable", reference="local", verified_by="operator:cli", verified_at="",
    )
    eng.transition("validation_passed", actor="operator:cli")
    eng.approved_by = "operator:cli"
    eng.transition("activate", actor="operator:cli")

    data = eng.to_dict()  # status="active" tersimpan di sini, BELUM expired
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    eng.time_window = TimeWindow(expires_at=future)
    data = eng.to_dict()

    past_data = dict(data)
    past_data["time_window"] = {"not_before": None, "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    restored = Engagement.from_dict(past_data)
    assert restored.status == EngagementStatus.EXPIRED


# ---------------------------------------------------------------------------
# EngagementStore -- CRUD
# ---------------------------------------------------------------------------

def test_save_and_fetch_roundtrip(tmp_path):
    store = _store(tmp_path)
    eng = _draft(targets=[TargetScope(pattern="example.com")])
    store.save(eng)

    fetched = store.fetch(eng.id)
    assert fetched is not None
    assert fetched.id == eng.id
    assert fetched.name == eng.name
    assert fetched.status == EngagementStatus.DRAFT


def test_fetch_nonexistent_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.fetch("ENG-doesnotexist") is None


def test_save_persists_transition_after_reload(tmp_path):
    """Bukti fetch-fresh: transition() TIDAK otomatis persist -- eksplisit
    save() dibutuhkan, dan fetch() setelah save() mencerminkan state baru."""
    store = _store(tmp_path)
    eng = _draft(targets=[TargetScope(pattern="example.com")])
    store.save(eng)
    eng.transition("submit_for_validation", actor="operator:cli")

    stale = store.fetch(eng.id)
    assert stale.status == EngagementStatus.DRAFT, "belum di-save() ulang, harus masih DRAFT di storage"

    store.save(eng)
    fresh = store.fetch(eng.id)
    assert fresh.status == EngagementStatus.VALIDATING


def test_fetch_returns_new_object_each_call_not_cached(tmp_path):
    """Bukti eksplisit 'selalu fresh, tidak pernah cache' -- dua fetch()
    berturut-turut mengembalikan instance OBJECT berbeda (bukan referensi
    yang sama), meniadakan risiko caller memutasi objek yang tersimpan
    diam-diam di store."""
    store = _store(tmp_path)
    eng = _draft(targets=[TargetScope(pattern="example.com")])
    store.save(eng)

    fetched_1 = store.fetch(eng.id)
    fetched_2 = store.fetch(eng.id)
    assert fetched_1 is not fetched_2
    fetched_1.notes = "mutated locally, should not affect store"
    fetched_3 = store.fetch(eng.id)
    assert fetched_3.notes == ""


def test_list_engagements_filters_by_status(tmp_path):
    store = _store(tmp_path)
    draft = _draft(targets=[TargetScope(pattern="a.com")])
    store.save(draft)

    validating = _draft(targets=[TargetScope(pattern="b.com")])
    validating.transition("submit_for_validation", actor="operator:cli")
    store.save(validating)

    all_engagements = store.list_engagements()
    assert len(all_engagements) == 2

    only_draft = store.list_engagements(status="draft")
    assert len(only_draft) == 1
    assert only_draft[0].id == draft.id


def test_delete_removes_engagement(tmp_path):
    store = _store(tmp_path)
    eng = _draft(targets=[TargetScope(pattern="example.com")])
    store.save(eng)
    assert store.fetch(eng.id) is not None
    store.delete(eng.id)
    assert store.fetch(eng.id) is None


def test_schema_ok(tmp_path):
    store = _store(tmp_path)
    assert store.schema_ok() is True


def test_db_file_created_and_secured(tmp_path):
    store = _store(tmp_path)
    assert store.db_path.exists()
