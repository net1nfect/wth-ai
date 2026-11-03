"""Regression tests — WU-203 (DATA-03): canonical secret redaction (single
source of truth + `EvidencePipeline.content` gap) & recon SSRF guard.

Dua concern di satu WU sesuai daftar P2 WORK-UNITS.md:
(a) Redaksi: SEBELUM WU ini ada 4 implementasi `redact*()` berbeda pola, dan
    `EvidencePipeline` membangun `Evidence.content` dari output tool mentah
    TANPA redaksi sama sekali (lalu mengalir ke findings yang dipersist ke
    disk). Fix: `wth_ai/redaction.py` kanonik (superset pola terlengkap),
    4 situs delegasi ke situ, `EvidencePipeline` redaksi `content`.
(b) SSRF: `recon/http.py::fetch()` GET/HEAD ke URL tanpa cek host internal +
    `allow_redirects=True`. Fix: guard host private/reserved (IP literal
    offline, hostname via getaddrinfo) + validasi tiap hop redirect.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.executor.evidence import EvidencePipeline
from wth_ai.models import ExecutorResult
from wth_ai.recon.http import fetch, host_is_blocked
from wth_ai.redaction import redact_secrets


# ---------------------------------------------------------------------------
# Canonical redact_secrets() — superset pola
# ---------------------------------------------------------------------------

def test_redacts_key_value_secret():
    assert "sk-live-abc" not in redact_secrets("api_key=sk-live-abc123")
    assert "REDACTED" in redact_secrets("api_key=sk-live-abc123")


def test_redacts_bearer_token():
    out = redact_secrets("Authorization: Bearer abcDEF123._-token")
    assert "abcDEF123" not in out


def test_redacts_aws_access_key():
    out = redact_secrets("cred AKIAIOSFODNN7EXAMPLE end")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_redacts_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijkl"
    out = redact_secrets(jwt)
    assert jwt not in out


def test_redact_idempotent():
    once = redact_secrets("token=supersecret")
    twice = redact_secrets(once)
    assert once == twice


def test_redact_handles_none_and_empty():
    assert redact_secrets("") == ""
    assert redact_secrets(None) == ""  # type: ignore[arg-type]


def test_clean_text_unchanged():
    clean = "GET /api/users returned 200 with 3 records"
    assert redact_secrets(clean) == clean


# ---------------------------------------------------------------------------
# 4 situs lama delegasi ke kanonik (non-regresi + coverage naik)
# ---------------------------------------------------------------------------

def test_all_four_legacy_sites_delegate_to_canonical():
    from wth_ai.evidence.vault import EvidenceVault
    from wth_ai.recon.runner import redact as recon_redact
    from wth_ai.rpc.execute_server import redact_secrets as rpc_redact
    from wth_ai.scope_manager import ScopeManager

    sample = "api_key=sk-secret AKIAIOSFODNN7EXAMPLE"
    expected = redact_secrets(sample)
    # scope_manager (butuh instance)
    assert ScopeManager().redact_secrets(sample) == expected
    # recon/runner module fn
    assert recon_redact(sample) == expected
    # rpc/execute_server module fn (nama di-reexport, diimpor test lain)
    assert rpc_redact(sample) == expected
    # EvidenceVault instance method
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        vault = EvidenceVault("sess-redact", base_dir=Path(tmp))
        assert vault.redact(sample) == expected


def test_scope_manager_now_also_redacts_aws_and_jwt():
    """Bukti coverage NAIK: scope_manager dulu cuma 2 pola (tanpa AWS/JWT);
    sekarang lewat kanonik jadi ikut menutup keduanya."""
    from wth_ai.scope_manager import ScopeManager

    out = ScopeManager().redact_secrets("leak AKIAIOSFODNN7EXAMPLE here")
    assert "AKIAIOSFODNN7EXAMPLE" not in out


# ---------------------------------------------------------------------------
# EvidencePipeline.content gap (inti temuan critique)
# ---------------------------------------------------------------------------

def test_evidence_content_redacted_from_executor_evidence_lines():
    result = ExecutorResult(
        task_id="T-1", status="success", permission_check={},
        commands_used=[], output_summary="",
        evidence=["found api_key=sk-super-secret-1234 in response"],
        observations=[], risk="medium", recommended_next_step="", errors="none",
    )
    items = EvidencePipeline().from_executor_result(result)
    assert items, "harus menghasilkan evidence"
    assert "sk-super-secret-1234" not in items[0].content
    assert "REDACTED" in items[0].content


def test_evidence_content_redacted_from_output_summary_fallback():
    result = ExecutorResult(
        task_id="T-2", status="success", permission_check={},
        commands_used=[], output_summary="dump: password=hunter2trustno1",
        evidence=[], observations=[], risk="low", recommended_next_step="", errors="none",
    )
    items = EvidencePipeline().from_executor_result(result)
    assert items
    assert "hunter2trustno1" not in items[0].content


def test_evidence_classification_still_uses_original_line():
    """Redaksi TIDAK boleh mengubah klasifikasi/severity — secret di baris
    tidak boleh menggeser evidence_type. Baris SQLi-signal dengan secret tetap
    diklasifikasi sqli_signal, hanya content-nya yang ter-redaksi."""
    result = ExecutorResult(
        task_id="T-3", status="success", permission_check={},
        commands_used=[], output_summary="",
        evidence=["mysql syntax error near token=leakedsecret123"],
        observations=[], risk="medium", recommended_next_step="", errors="none",
    )
    items = EvidencePipeline().from_executor_result(result)
    assert items[0].evidence_type == "sqli_signal"
    assert "leakedsecret123" not in items[0].content


def test_redacted_content_propagates_to_findings_candidates():
    """End-to-end: content ter-redaksi ikut ke findings candidate (description/
    evidence) yang dipersist -- membuktikan secret tidak sampai ke disk."""
    result = ExecutorResult(
        task_id="T-4", status="success", permission_check={},
        commands_used=[], output_summary="",
        evidence=["critical: leaked authorization=Bearer sk-abcdef123456"],
        observations=[], risk="high", recommended_next_step="", errors="none",
    )
    pipeline = EvidencePipeline()
    evidence = pipeline.from_executor_result(result)
    cands = pipeline.to_findings_candidates(evidence)
    assert cands, "severity critical harus jadi candidate"
    blob = str(cands)
    assert "sk-abcdef123456" not in blob


# ---------------------------------------------------------------------------
# SSRF guard — host_is_blocked() unit
# ---------------------------------------------------------------------------

def test_loopback_and_metadata_and_private_blocked():
    for h in ("127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.1", "172.16.0.1", "0.0.0.0", "::1"):
        assert host_is_blocked(h) is not None, f"{h} seharusnya diblok"


def test_public_ip_literal_allowed():
    assert host_is_blocked("8.8.8.8") is None
    assert host_is_blocked("1.1.1.1") is None


def test_empty_host_blocked():
    assert host_is_blocked("") is not None


def test_hostname_not_resolved_when_resolve_false():
    assert host_is_blocked("example.com", resolve=False) is None


def test_hostname_resolving_to_private_is_blocked():
    """DNS-rebinding-style: hostname yang resolve ke IP internal diblok."""
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
        assert host_is_blocked("evil.example.com", resolve=True) is not None


def test_hostname_resolving_to_public_allowed():
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        assert host_is_blocked("example.com", resolve=True) is None


def test_unresolvable_hostname_returns_none():
    import socket as _socket
    with patch("socket.getaddrinfo", side_effect=_socket.gaierror("nope")):
        assert host_is_blocked("nonexistent.invalid", resolve=True) is None


# ---------------------------------------------------------------------------
# SSRF guard — fetch() integration
# ---------------------------------------------------------------------------

def test_fetch_blocks_internal_ip_literal_even_in_test_mode():
    """IP literal internal diklasifikasi offline -> diblok bahkan di test mode
    (sebelum short-circuit test-mode)."""
    r = fetch("http://127.0.0.1/admin")
    assert r.error is not None
    assert "SSRF" in r.error


def test_fetch_blocks_metadata_endpoint():
    r = fetch("http://169.254.169.254/latest/meta-data/")
    assert r.error is not None
    assert "SSRF" in r.error


def test_fetch_normal_host_hits_test_mode_shortcircuit():
    """Host publik-shaped: guard lolos (tidak resolve DNS di test mode), lalu
    kena short-circuit test-mode seperti perilaku lama."""
    r = fetch("https://example.com")
    assert r.error == "test-mode: network disabled"


def test_fetch_rejects_non_get_head():
    r = fetch("https://example.com", method="POST")
    assert r.error is not None
    assert "not allowed" in r.error
