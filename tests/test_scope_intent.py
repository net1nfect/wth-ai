"""Scope intent parsing tests."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.scope_intent import parse_scope_intent


def test_parse_set_target_url():
    """WU-011 (AGENT-01): "set target X" TIDAK menyebut otorisasi apa pun --
    sebelumnya _args_from_target() hardcode authorized=True di semua jalur,
    sekarang fail-closed (MISSING -> DENY) sampai teks eksplisit menyebut
    "authorized"/"izin" (lihat test_parse_set_target_url_with_explicit_authorization)."""
    args = parse_scope_intent("set target https://universitas-a.ssc.sevimaplatform.com/gate/login")
    assert args is not None
    assert args["target"] == "universitas-a.ssc.sevimaplatform.com"
    assert args["authorized"] is False
    assert "universitas-a.ssc.sevimaplatform.com" in args["in_scope"]
    assert "entry_url=" in args.get("notes", "")


def test_parse_set_target_url_with_explicit_authorization():
    """Positive/compatibility: authorized masih bisa jadi True kalau teks
    eksplisit menyebutnya -- fix ini tidak menghapus kapabilitas itu. Pakai
    pola "scope X authorized" (bukan "set target URL authorized") supaya
    tidak collide dengan quirk parsing regex target-URL yang sudah ada
    sebelum WU-011 (kata "authorized" ikut tertangkap sebagai bagian target
    kalau ditulis nempel di belakang URL -- observasi baru, di luar scope
    WU ini, bukan security control)."""
    args = parse_scope_intent("scope example.com authorized")
    assert args is not None
    assert args["target"] == "example.com"
    assert args["authorized"] is True


def test_parse_scope_domain_authorized():
    args = parse_scope_intent("scope example.com authorized")
    assert args is not None
    assert args["target"] == "example.com"


def test_parse_bare_url():
    args = parse_scope_intent("https://foo.example.org/path")
    assert args is not None
    assert args["target"] == "foo.example.org"


def test_parse_unrelated_returns_none():
    assert parse_scope_intent("hello world") is None


def test_supervisor_fast_scope_via_orchestrator():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/target https://test.example.com/login")
    assert "test.example.com" in out
    assert orch.state.scope.target == "test.example.com"
