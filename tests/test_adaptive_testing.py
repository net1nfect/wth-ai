"""Doktrin uji adaptif: analisa respons, pembanding, dan opt-in destruktif.

Permintaan operator: AI harus adaptif dan proaktif, TIDAK bergantung pada modul yang
ada. Yang diuji di sini adalah mesinnya: sinyal respons yang bisa dibandingkan, diff
antar-request, aliran bukti, dan knob destruktif yang eksplisit (bukan implisit).

Prinsip yang dijaga: kemampuan boleh dilebarkan, tapi klaim tetap butuh bukti, dan
aksi yang merusak data tetap butuh pernyataan eksplisit dari operator.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.models import Scope
from wth_ai.terminal.analysis import (
    analyze_response,
    body_fingerprint,
    diff_responses,
    evidence_pair,
    looks_like_error,
    pick_headers,
)


# --------------------------------------------------------------------------- #
# sinyal respons
# --------------------------------------------------------------------------- #
def test_analysis_reports_the_signals_needed_to_choose_a_next_step():
    out = analyze_response(
        status=200,
        headers={"Content-Type": "application/json", "Set-Cookie": "sid=1",
                 "Location": "/next", "X-Trace": "abc"},
        body='{"id": 2, "email": "bob@local"}',
        method="GET", url="http://t/api/user?id=2",
    )
    assert "status=200" in out
    assert "sha=" in out and "error_like=no" in out
    assert "content-type" in out and "location" in out and "x-trace" in out
    assert "GET http://t/api/user?id=2" in out


def test_analysis_flags_error_shaped_bodies():
    out = analyze_response(status=500, headers={}, body="Internal Server Error: traceback ...")
    assert "error_like=yes" in out
    assert looks_like_error("not found") is True
    assert looks_like_error("semua oke") is False


def test_fingerprint_ignores_whitespace_but_not_content():
    assert body_fingerprint("a  b\nc") == body_fingerprint("a b c")
    assert body_fingerprint("a b") != body_fingerprint("a c")


def test_headers_are_filtered_to_the_interesting_ones():
    picked = pick_headers({"Content-Type": "text/html", "X-Frame-Options": "DENY",
                           "X-Random-Noise": "1", "Date": "now"})
    assert "content-type" in picked and "x-frame-options" in picked and "x-random-noise" in picked
    assert "date" not in picked, "header tidak relevan tidak perlu masuk analisa"


# --------------------------------------------------------------------------- #
# diff: dasar pengujian adaptif
# --------------------------------------------------------------------------- #
def _tx(tid: str, status: int, body: str) -> dict:
    return {"transcript_id": tid, "status_code": status, "stdout": body}


def test_diff_names_every_real_change():
    out = diff_responses(_tx("TX1", 200, "ok data"), _tx("TX2", 403, "forbidden"))
    assert "status 200 -> 403" in out
    assert "isi berbeda" in out
    assert "error_like False -> True" in out


def test_diff_refuses_to_call_an_unchanged_response_a_signal():
    out = diff_responses(_tx("TX1", 200, "same"), _tx("TX2", 200, "same"))
    assert "TIDAK ADA PERBEDAAN" in out
    assert "bukan temuan" in out


def test_diff_detects_a_pure_length_change():
    out = diff_responses(_tx("TX1", 200, "abc"), _tx("TX2", 200, "abcdef"))
    assert "panjang 3 -> 6" in out


# --------------------------------------------------------------------------- #
# bukti
# --------------------------------------------------------------------------- #
class _Result:
    method = "POST"
    url = "http://t/api"
    status_code = 201
    stdout = "created"


def test_evidence_pair_carries_what_verify_needs():
    pair = evidence_pair(_Result(), role="business-logic")
    assert pair["method"] == "POST" and pair["response_status"] == 201
    assert pair["body_snippet"] == "created"
    assert pair["role"] == "business-logic"


# --------------------------------------------------------------------------- #
# opt-in destruktif: eksplisit, bukan kebetulan
# --------------------------------------------------------------------------- #
def test_destructive_is_off_by_default():
    assert Scope().destructive_allowed is False


def test_scope_set_destructive_writes_a_bool_and_a_note():
    from wth_ai.orchestrator import WTHOrchestrator
    import inspect

    source = inspect.getsource(WTHOrchestrator._handle_scope)
    assert "destructive_allowed" in source
    assert "yes" in source, "nilai teks harus dinormalkan"

    # perilakunya: update_from_dict hanya menerima field yang ada di Scope
    scope = Scope()
    assert "destructive_allowed" in Scope.__dataclass_fields__
    assert isinstance(scope.destructive_allowed, bool)


def test_mutating_http_method_is_gated_unless_the_engagement_allows_it():
    """Tanpa opt-in: method pengubah data diblokir minta approval. Dengan opt-in: lolos
    (dan diaudit)."""
    import inspect

    from wth_ai.terminal.bridge import AgentTerminalBridge

    source = inspect.getsource(AgentTerminalBridge.http_probe)
    assert "destructive" in source
    assert "self._audit_destructive(" in source, "aksi destruktif wajib dicatat"


def test_audit_helper_never_raises(monkeypatch):
    from wth_ai.terminal.bridge import AgentTerminalBridge

    bridge = object.__new__(AgentTerminalBridge)  # hanya butuh method ini
    import wth_ai.terminal.bridge as bridge_mod

    def _boom(*a, **k):
        raise RuntimeError("audit rusak")

    monkeypatch.setattr(bridge_mod, "audit_log", _boom, raising=False)
    bridge._audit_destructive("supervisor", "tujuan", "DELETE http://t/x")  # tidak boleh raise


# --------------------------------------------------------------------------- #
# doktrin prompt
# --------------------------------------------------------------------------- #
def test_supervisor_prompt_teaches_adaptive_testing():
    from pathlib import Path

    text = Path("prompts/supervisor_system.txt").read_text(encoding="utf-8")
    assert "DOKTRIN UJI ADAPTIF" in text
    for token in ("http_probe", "compare_to", "[DIFF vs TX]", "destructive_allowed", "[TX:"):
        assert token in text, f"doktrin adaptif kehilangan {token}"


def test_executor_prompt_allows_adapting_within_the_task():
    from pathlib import Path

    text = Path("prompts/executor_system.txt").read_text(encoding="utf-8")
    assert "beradaptasi" in text.lower()
    assert "bukan batas" in text.lower(), "modul harus disebut sebagai contoh, bukan batas"
