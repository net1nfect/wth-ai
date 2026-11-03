"""Regression tests — WU-105: Audit log durable (`AuditLogSink`, PRD-006/
PRD-011, ADR-008).

Bug baru ditemukan & ditutup sekaligus saat implementasi (bukan bagian asli
WU-006, tapi terungkap saat wiring WU ini): `LoopStateMachine._emit_invalid()`
(WU-006) sudah memanggil `bus.emit("invalid_state_transition", ...)` sejak
awal, TAPI `"invalid_state_transition"` TIDAK ADA di `EVENT_TYPES` --
`EventBus.emit()`/`make_event()` SELALU raise `ValueError` untuk tipe tak
dikenal, ditelan diam-diam oleh try/except `_emit_invalid()` sendiri
(sengaja defensif supaya bus rusak tidak merusak state machine) -- event itu
TIDAK PERNAH sekali pun sampai ke subscriber manapun sebelum fix ini.
Lolos dari test WU-006 karena test itu pakai `FakeBus` buatan sendiri yang
tidak mereplikasi validasi tipe EventBus asli.

Scope decision: `AuditLogSink` DIBANGUN, DIUJI PENUH, dan siap pakai
(`get_event_bus().subscribe(AuditLogSink())` satu baris) -- TAPI SENGAJA
TIDAK di-auto-wire ke `WTHOrchestrator.__init__()` di WU ini. Alasan: setiap
test yang mengonstruksi WTHOrchestrator (jumlahnya besar di seluruh suite
ini) akan ikut menulis SQLite pada tiap event kalau di-wire otomatis --
risiko pelambatan/side-effect test yang nyata, bukan hipotetis. Pola sama
dengan WU-101/102/106: skeleton yang genuinely bekerja + teruji penuh,
integrasi startup ditunda ke WU follow-up yang bisa mempertimbangkan opt-in/
config flag dengan lebih hati-hati.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.audit_log import AuditLogSink
from wth_ai.events import EventBus, get_event_bus, reset_event_bus
from wth_ai.loop.state_machine import LoopState, LoopStateMachine


# ---------------------------------------------------------------------------
# EVENT_TYPES fix -- regresi-guard utama
# ---------------------------------------------------------------------------

def test_invalid_state_transition_no_longer_raises_valueerror():
    bus = EventBus()
    event = bus.emit("invalid_state_transition", source="test", data={"from": "idle", "to": "executing"})
    assert event.type == "invalid_state_transition"


def test_loop_state_machine_invalid_transition_reaches_real_subscriber():
    """Bukti end-to-end: LoopStateMachine._emit_invalid() (WU-006) yang
    sebelumnya tertelan diam-diam sekarang BENAR-BENAR sampai ke subscriber
    EventBus ASLI (bukan FakeBus buatan test). Event di-emit SEBELUM
    InvalidTransitionError di-raise (WU-006, WTH_TEST_MODE=1 aktif di file
    test ini) -- raise itu sendiri yang dibuktikan diverifikasi tetap terjadi
    seperti dirancang."""
    import pytest

    from wth_ai.loop.state_machine import InvalidTransitionError

    bus = EventBus()
    received = []
    bus.subscribe(lambda e: received.append(e))

    sm = LoopStateMachine(bus=bus)
    with pytest.raises(InvalidTransitionError):
        sm.transition(LoopState.EXECUTING)  # IDLE -> EXECUTING ilegal

    assert len(received) == 1
    assert received[0].type == "invalid_state_transition"
    assert received[0].data["from"] == "idle"
    assert received[0].data["to"] == "executing"


def test_invalid_state_transition_has_warning_severity():
    bus = EventBus()
    event = bus.emit("invalid_state_transition", source="test", data={})
    assert event.severity == "warning"


# ---------------------------------------------------------------------------
# EngagementLoop wiring -- LoopStateMachine benar-benar pakai global bus
# ---------------------------------------------------------------------------

def test_engagement_loop_wires_global_event_bus(monkeypatch):
    from unittest.mock import MagicMock

    from wth_ai.loop.engagement_loop import EngagementLoop

    reset_event_bus()
    try:
        loop = EngagementLoop(MagicMock())
        assert loop.sm.bus is get_event_bus()
    finally:
        reset_event_bus()


# ---------------------------------------------------------------------------
# AuditLogSink -- CRUD dasar
# ---------------------------------------------------------------------------

def _sink(tmp_path) -> AuditLogSink:
    return AuditLogSink(db_path=tmp_path / "audit.sqlite")


def test_schema_ok(tmp_path):
    assert _sink(tmp_path).schema_ok() is True


def test_record_and_query_roundtrip(tmp_path):
    from wth_ai.events import Event

    sink = _sink(tmp_path)
    event = Event(type="job.created", source="test", data={"target": "example.com"})
    sink.record(event)

    rows = sink.query(event_type="job.created")
    assert len(rows) == 1
    assert rows[0]["id"] == event.id
    assert rows[0]["source"] == "test"
    import json
    assert json.loads(rows[0]["data"])["target"] == "example.com"


def test_count(tmp_path):
    from wth_ai.events import Event

    sink = _sink(tmp_path)
    assert sink.count() == 0
    sink.record(Event(type="job.created"))
    sink.record(Event(type="job.completed"))
    assert sink.count() == 2


def test_idempotent_record_same_event_id_no_duplicate(tmp_path):
    """INSERT OR IGNORE -- mencatat event dengan id SAMA dua kali (mis. dua
    instance sink berbeda menulis ke db yang sama) tidak menghasilkan baris
    duplikat."""
    from wth_ai.events import Event

    sink = _sink(tmp_path)
    event = Event(type="job.created")
    sink.record(event)
    sink.record(event)  # id sama persis
    assert sink.count() == 1


def test_tainted_column_null_when_not_set(tmp_path):
    """Catatan jujur: tainted SELALU NULL sampai context.tainted (WU-007
    remainder) benar-benar ada -- dikunci eksplisit di sini."""
    from wth_ai.events import Event

    sink = _sink(tmp_path)
    sink.record(Event(type="job.created", data={}))
    rows = sink.query()
    assert rows[0]["tainted"] is None


def test_tainted_column_set_when_present_in_event_data(tmp_path):
    """Compatibility/forward-readiness: kolom sudah bisa dipakai KALAU suatu
    saat ada pemanggil yang mengisi event.data['tainted']."""
    from wth_ai.events import Event

    sink = _sink(tmp_path)
    sink.record(Event(type="job.created", data={"tainted": True}))
    rows = sink.query()
    assert rows[0]["tainted"] == 1


# ---------------------------------------------------------------------------
# AuditLogSink sebagai EventBus subscriber -- integrasi
# ---------------------------------------------------------------------------

def test_sink_subscribed_to_bus_captures_events_automatically(tmp_path):
    bus = EventBus()
    sink = _sink(tmp_path)
    bus.subscribe(sink)

    bus.emit("job.created", source="test", data={"type": "recon"})
    bus.emit("job.completed", source="test", data={})

    assert sink.count() == 2
    assert len(sink.query(event_type="job.created")) == 1


def test_sink_subscribed_captures_invalid_state_transition_end_to_end(tmp_path):
    """Rantai penuh: LoopStateMachine -> EventBus -> AuditLogSink, ketiganya
    ditutup WU ini secara end-to-end, bukan cuma unit terisolasi."""
    import pytest

    from wth_ai.loop.state_machine import InvalidTransitionError

    bus = EventBus()
    sink = _sink(tmp_path)
    bus.subscribe(sink)

    sm = LoopStateMachine(bus=bus)
    with pytest.raises(InvalidTransitionError):
        sm.transition(LoopState.EXECUTING)  # ilegal dari IDLE

    rows = sink.query(event_type="invalid_state_transition")
    assert len(rows) == 1


def test_failing_sink_does_not_break_other_subscribers(tmp_path):
    """Compatibility: EventBus.publish() sendiri sudah isolasi per-subscriber
    (try/except) -- pastikan sink yang error tidak mematikan subscriber lain,
    bukan mengubah perilaku EventBus, cuma memverifikasi tetap berlaku."""
    bus = EventBus()

    def _broken_sink(event):
        raise RuntimeError("disk full")

    received = []
    bus.subscribe(_broken_sink)
    bus.subscribe(lambda e: received.append(e))
    bus.emit("job.created", source="test", data={})
    assert len(received) == 1
