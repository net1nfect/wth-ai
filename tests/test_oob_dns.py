"""F2 lanjutan — DNS listener Callback Center (OOB via resolusi nama).

Dipakai untuk membuktikan blind XXE / blind SSRF / RCE yang memicu *lookup* DNS
(tanpa koneksi HTTP). Kontrak:

* Listener jawab pertanyaan A dan TXT; hit tercatat dengan token + qname.
  Jawaban selalu dikembalikan supaya resolver pemanggil tidak menggantung.
* Semua hit masuk jalur yang sama dengan HTTP (`OOBStore` → redaksi → TX).
* Test memakai port ephemeral di 127.0.0.1 (tidak butuh root, tidak menyentuh
  resolver sistem).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import socket  # noqa: E402
import struct  # noqa: E402

import pytest  # noqa: E402

from wth_ai.oob.dns import DNSListener, build_query, parse_qname
from wth_ai.oob.service import get_oob_service, reset_oob_service
from wth_ai.terminal.transcript import TerminalTranscriptStore  # noqa: E402
from wth_ai.oob.store import OOBStore  # noqa: E402


def _store(tmp_path) -> OOBStore:
    return OOBStore(base_dir=tmp_path / "oob")


# ── unit: encoder/decoder DNS ────────────────────────────────────────────


def test_build_query_roundtrips_through_parse():
    pkt = build_query("abc123.oob.test", qtype=1)
    qname, qtype = parse_qname(pkt)
    assert qname == "abc123.oob.test"
    assert qtype == 1


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_qname(b"\x00\x01\x02")


# ── listener: jawaban + pencatatan ───────────────────────────────────────


def _ask(port: int, qname: str, qtype: int = 1) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(build_query(qname, qtype), ("127.0.0.1", port))
        return sock.recv(4096)
    finally:
        sock.close()


def test_dns_listener_answers_and_records_hit(tmp_path):
    store = _store(tmp_path)
    listener = DNSListener(store, host="127.0.0.1", port=0)
    listener.start()
    try:
        token = store.create_token(label="dns-xxe")["token"]
        resp = _ask(listener.port, f"{token}.oob.test")
        assert resp[:2] == b"\x00\x00"[:1] + resp[1:2]  # ada respons
        assert len(resp) > 12, "respons DNS harus berisi answer"
        hits = store.hits(token)
        assert len(hits) == 1
        assert hits[0]["method"] == "DNS"
        assert token in hits[0]["path"]
        assert hits[0]["query"].startswith("A ")
    finally:
        listener.stop()


def test_dns_listener_answers_txt_queries(tmp_path):
    store = _store(tmp_path)
    listener = DNSListener(store, host="127.0.0.1", port=0)
    listener.start()
    try:
        token = store.create_token(label="dns-txt")["token"]
        resp = _ask(listener.port, f"{token}.oob.test", qtype=16)
        assert len(resp) > 12
        assert len(store.hits(token)) == 1
    finally:
        listener.stop()


def test_dns_unknown_token_still_answered_and_recorded(tmp_path):
    store = _store(tmp_path)
    listener = DNSListener(store, host="127.0.0.1", port=0)
    listener.start()
    try:
        resp = _ask(listener.port, "ngawur.oob.test")
        assert len(resp) > 12, "query tak dikenal tetap dijawab (anti-oracle)"
        recs = store.recent_hits()
        assert len(recs) == 1 and recs[0]["matched"] is False
    finally:
        listener.stop()


def test_dns_listener_stop_is_clean(tmp_path):
    store = _store(tmp_path)
    listener = DNSListener(store, host="127.0.0.1", port=0)
    listener.start()
    port = listener.port
    listener.stop()
    assert not listener.running
    with pytest.raises(OSError):
        _ask(port, "after-stop.oob.test")


def test_token_is_extracted_from_first_label(tmp_path):
    store = _store(tmp_path)
    listener = DNSListener(store, host="127.0.0.1", port=0)
    listener.start()
    try:
        token = store.create_token(label="label-extract")["token"]
        _ask(listener.port, f"{token}.sub.deep.oob.test")
        hits = store.hits(token)
        assert len(hits) == 1
    finally:
        listener.stop()


# ── wiring: service + tool agent + slash ─────────────────────────────────


def test_service_start_dns_and_token_name(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        svc = get_oob_service()
        st = svc.start_dns(port=0)
        assert st["running"] and st["port"] > 0
        entry = svc.create(label="dns-svc")
        assert entry["dns"] == f"{entry['token']}.oob.local"
        _ask(svc.dns.port, entry["dns"])  # simulasi target melakukan resolusi
        assert len(svc.store.hits(entry["token"])) == 1
        assert svc.stop_dns() is True
    finally:
        reset_oob_service()


def test_dispatch_oob_dns_and_check(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        from types import SimpleNamespace

        from wth_ai.agents.supervisor_dispatch import _dispatch

        transcripts = TerminalTranscriptStore("s1", base_dir=tmp_path / "home" / "data" / "engagements")
        ctx = SimpleNamespace(
            terminal=SimpleNamespace(transcripts=transcripts),
            state=SimpleNamespace(session_id="s1", scope=SimpleNamespace(target="app.test")),
        )
        out = _dispatch(ctx, "oob_dns", {"port": 0}, approved=False)
        assert ".oob.local" in out and "token:" in out
        svc = get_oob_service()
        token = svc.store.list_tokens()[0]["token"]
        _ask(svc.dns.port, f"{token}.oob.local")  # target resolve nama callback
        out2 = _dispatch(ctx, "oob_check", {"token": token}, approved=False)
        assert "[TX:" in out2
        assert "hits: 1" in out2
    finally:
        reset_oob_service()


def test_slash_oob_dns_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.orchestrator import WTHOrchestrator

        orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
        out = orch.handle_input("/oob dns start --port 0")
        assert "DNS listener aktif" in out
        out2 = orch.handle_input("/oob dns")
        assert "RUNNING" in out2
        out3 = orch.handle_input("/oob dns stop")
        assert "dihentikan" in out3
    finally:
        reset_oob_service()
