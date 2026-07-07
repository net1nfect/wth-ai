"""FTS session memory tests."""

from __future__ import annotations

import pytest

from wth_ai.memory.fts import SessionFtsIndex


def test_fts_index_and_search(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    idx = SessionFtsIndex()
    idx.index_turn("sess-1", "/recon example.com", "recon job created abc123")
    hits = idx.search("example")
    assert hits
    assert hits[0]["session_key"] == "sess-1"


def test_fts_no_plaintext_secrets_in_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    idx = SessionFtsIndex()
    masked = "api_key=***REDACTED***"
    idx.index_snippet("s", "assistant", masked)
    hits = idx.search("REDACTED")
    assert hits
    text = hits[0]["snippet"]
    assert "sk-live" not in text
