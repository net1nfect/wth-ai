"""Callback Center (F2) — OOB store, server, service, dan wiring tool/agent.

Semua test hermetis: listener hanya di 127.0.0.1 dengan port ephemeral
(`port=0`), store di `WTH_HOME`/`tmp_path` temporer. Tidak ada jaringan
eksternal — suite tetap hijau saat jaringan luar diblokir.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_NO_ANIMATION", "1")

from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

import requests  # noqa: E402

from wth_ai.oob import OOBCallbackServer, OOBStore, format_hits, reset_oob_service  # noqa: E402
from wth_ai.oob.service import get_oob_service  # noqa: E402
from wth_ai.terminal.transcript import TerminalTranscriptStore  # noqa: E402


def _store(tmp_path) -> OOBStore:
    return OOBStore(base_dir=tmp_path / "oob")


# ── store ────────────────────────────────────────────────────────────────


def test_token_create_get_and_persist(tmp_path):
    store = _store(tmp_path)
    entry = store.create_token(label="ssrf-1", ttl_minutes=30, session_id="s1", target="app.test")
    assert entry["token"]
    assert (tmp_path / "oob" / "tokens.json").exists()

    # instance baru membaca file yang sama (persistensi lintas proses)
    again = OOBStore(base_dir=tmp_path / "oob").get_token(entry["token"])
    assert again is not None
    assert again["label"] == "ssrf-1"
    assert again["expired"] is False


def test_token_expiry_is_live_and_hits_after_expiry_still_recorded(tmp_path):
    store = _store(tmp_path)
    entry = store.create_token(label="expire", ttl_minutes=0)  # langsung kedaluwarsa
    got = store.get_token(entry["token"])
    assert got is not None and got["expired"] is True
    store.record_interaction(token=entry["token"], method="GET", path="/c/x", remote="127.0.0.1")
    assert len(store.hits(entry["token"])) == 1  # bukti telat tetap tersimpan


def test_unknown_token_recorded_but_marked_unmatched(tmp_path):
    store = _store(tmp_path)
    rec = store.record_interaction(token="tidak-dikenal", method="GET", path="/c/tidak-dikenal", remote="10.0.0.1")
    assert rec["matched"] is False
    assert len(store.hits("tidak-dikenal")) == 1
    assert store.total_hits() == 1


# ── server ───────────────────────────────────────────────────────────────


def test_server_records_real_callback(tmp_path):
    store = _store(tmp_path)
    server = OOBCallbackServer(store, host="127.0.0.1", port=0)
    server.start()
    try:
        entry = store.create_token(label="live")
        r = requests.get(server.url_for(entry["token"]), timeout=5)
        assert r.status_code == 200
        r2 = requests.post(server.url_for(entry["token"]) + "/lanjutan", data="hello=1", timeout=5)
        assert r2.status_code == 200

        hits = store.hits(entry["token"])
        assert len(hits) == 2
        assert hits[0]["method"] == "GET"
        assert hits[1]["method"] == "POST"
        assert hits[1]["body"] == "hello=1"
        assert hits[0]["remote"] == "127.0.0.1"
        assert hits[1]["path"] == f"/c/{entry['token']}/lanjutan"
    finally:
        server.stop()
    assert not server.running


def test_unknown_token_gets_same_200_response(tmp_path):
    store = _store(tmp_path)
    server = OOBCallbackServer(store, host="127.0.0.1", port=0)
    server.start()
    try:
        r = requests.get(server.url_base + "/c/ngawur", timeout=5)
        assert r.status_code == 200
        assert len(store.hits("ngawur")) == 1
    finally:
        server.stop()


def test_secrets_are_redacted_in_headers_and_body(tmp_path):
    store = _store(tmp_path)
    server = OOBCallbackServer(store, host="127.0.0.1", port=0)
    server.start()
    try:
        entry = store.create_token(label="redact")
        secret = "SUPERSECRETVALUE123"
        requests.post(
            server.url_for(entry["token"]),
            headers={"Authorization": f"Bearer {secret}"},
            data=f"password={secret}&ok=1",
            timeout=5,
        )
        hits = store.hits(entry["token"])
        assert len(hits) == 1
        blob = str(hits[0])
        assert secret not in blob
        assert "***REDACTED***" in blob
    finally:
        server.stop()


# ── service + transcript ─────────────────────────────────────────────────


def test_service_token_url_and_transcript(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    svc = get_oob_service()
    svc.start()
    try:
        entry = svc.create(label="svc")
        assert entry["url"].startswith("http://127.0.0.1:")
        requests.get(entry["url"], timeout=5)

        transcripts = TerminalTranscriptStore("s1", base_dir=tmp_path / "home" / "data" / "engagements")
        hits = svc.store.hits(entry["token"])
        tx = svc.record_as_transcript(transcripts, token=entry["token"], hits=hits)
        assert tx.startswith("TX-")
        stored = transcripts.get(tx)
        assert stored is not None
        assert "OOB CALLBACK" in stored["stdout"]
        assert stored["actor"] == "oob"
    finally:
        svc.stop()
        reset_oob_service()


def test_hits_format_contains_evidence_lines(tmp_path):
    store = _store(tmp_path)
    entry = store.create_token(label="fmt")
    store.record_interaction(
        token=entry["token"], method="GET", path="/c/fmt", remote="127.0.0.1",
        headers={"User-Agent": "curl/8", "X-Waf": "nope"},
    )
    text = format_hits(entry, store.hits(entry["token"]))
    assert "OOB CALLBACK — fmt" in text
    assert "User-Agent: curl/8" in text
    assert "X-Waf: nope" in text


# ── wiring: tool dispatch + slash + registry ─────────────────────────────


class _Ctx:
    def __init__(self, transcripts):
        self.terminal = SimpleNamespace(transcripts=transcripts)
        self.state = SimpleNamespace(session_id="s1", scope=SimpleNamespace(target="app.test"))


def test_dispatch_oob_create_and_check(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        from wth_ai.agents.supervisor_dispatch import _dispatch

        transcripts = TerminalTranscriptStore("s1", base_dir=tmp_path / "home" / "data" / "engagements")
        ctx: Any = _Ctx(transcripts)

        out = _dispatch(ctx, "oob_create", {"label": "blind-ssrf"}, approved=False)
        assert "url:" in out and "token:" in out
        svc = get_oob_service()
        token = svc.store.list_tokens()[0]["token"]

        # belum ada hit
        out2 = _dispatch(ctx, "oob_check", {"token": token}, approved=False)
        assert "Belum ada hit" in out2

        # panggil callback-nya (simulasi server target yang menghubungi kita)
        requests.get(f"{svc.base_url}/c/{token}", timeout=5)
        out3 = _dispatch(ctx, "oob_check", {"token": token}, approved=False)
        assert "[TX:" in out3
        assert "hits: 1" in out3
    finally:
        reset_oob_service()


def test_tool_registry_exposes_oob_tools():
    from wth_ai.agents.tool_registry import ACTOR_TOOLS, TOOL_DEFINITIONS, tools_for_actor

    assert "oob_create" in TOOL_DEFINITIONS and "oob_check" in TOOL_DEFINITIONS
    assert "oob_create" in ACTOR_TOOLS["executor"] and "oob_check" in ACTOR_TOOLS["planner"]
    names = {t["function"]["name"] for t in tools_for_actor("supervisor")}
    assert {"oob_create", "oob_check"} <= names


def test_slash_oob_command_is_routable_and_works(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.orchestrator import WTHOrchestrator

        orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
        out = orch.handle_input("/oob status")
        assert "OOB Callback Center" in out

        out2 = orch.handle_input("/oob new test-cb")
        assert "http://127.0.0.1:" in out2

        token = orch.oob.store.list_tokens()[0]["token"]
        requests.get(f"{orch.oob.base_url}/c/{token}", timeout=5)
        out3 = orch.handle_input(f"/oob hits {token}")
        assert "[TX:" in out3

        out4 = orch.handle_input("/oob stop")
        assert "dihentikan" in out4.lower() or "tidak sedang jalan" in out4.lower()
    finally:
        reset_oob_service()
