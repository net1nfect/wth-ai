"""Regression tests — WU-107: vault→broker target credential injection for
`http_probe()` (PRD-011/ADR-008).

Bug asli: `http_probe()` sent request headers as-is -- a caller (LLM
tool-call args included) wanting to authenticate a target request had no
choice but to generate/pass the PLAINTEXT secret itself (e.g.
`{"Authorization": "Bearer sk-real-secret"}`), which then lives in LLM
context/conversation history. There was zero `vault:<name>` resolution
anywhere on the request-header path (confirmed via grep: every existing
`SecretVault.resolve()` call site is for provider/bot/OSINT-API secrets,
none for target-request headers). The LLM-facing tool schema didn't even
declare a `headers` parameter, making the gap doubly unreachable in
practice.

Scope decision (lihat docstring `AgentTerminalBridge._resolve_vault_headers()`
untuk rincian penuh): resolusi HANYA untuk `headers` (kasus realistis utama --
Authorization/session cookie) di `http_probe()`. TIDAK termasuk `body`, dan
TIDAK termasuk substitusi inline di `run_shell()`'s command string (bentuk
substitusi yang berbeda & lebih berisiko -- potensi collision substring
parsial) -- ini gap yang beda, didokumentasikan sebagai follow-up, bukan
diam-diam diperluas ke sana. Gate `Capability.allow_credential_injection`
per-actor (target architecture penuh, DATA-MODEL.md) TIDAK diimplementasikan
di sini -- butuh `Engagement.credentials_ref` (WU-101's broker integration
yang sengaja belum ada) + Capability resolution per-actor (WU-007/WU-011
remainder) untuk berarti nyata; membangunnya sekarang jadi architecture leap
kosong (disiplin sama seperti WU-102's scope decision sendiri).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.agents.tool_registry import TOOL_DEFINITIONS
from wth_ai.config import WTHConfig
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge
from wth_ai.vault import SecretVault


def _bridge() -> AgentTerminalBridge:
    config = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    return AgentTerminalBridge(config, PermissionManager(), scope, "test-http-vault")


def _vault(tmp_path, monkeypatch) -> SecretVault:
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    return SecretVault()


def _mock_response():
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.text = "ok"
    return resp


# ---------------------------------------------------------------------------
# _resolve_vault_headers() -- unit level
# ---------------------------------------------------------------------------

def test_no_vault_refs_passthrough_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    bridge = _bridge()
    headers = {"X-Custom": "literal-value", "Accept": "application/json"}
    resolved, err = bridge._resolve_vault_headers(headers)
    assert resolved == headers
    assert err == ""


def test_vault_ref_resolves_to_plaintext(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.set("target_token", "sk-real-secret-value")
    bridge = _bridge()
    resolved, err = bridge._resolve_vault_headers({"Authorization": "vault:target_token"})
    assert err == ""
    assert resolved == {"Authorization": "sk-real-secret-value"}


def test_missing_vault_ref_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    bridge = _bridge()
    resolved, err = bridge._resolve_vault_headers({"Authorization": "vault:does_not_exist"})
    assert resolved is None
    assert "Cannot resolve" in err
    assert "vault:does_not_exist" in err


def test_mixed_literal_and_vault_headers(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.set("target_token", "sk-real-secret-value")
    bridge = _bridge()
    resolved, err = bridge._resolve_vault_headers({
        "Authorization": "vault:target_token",
        "X-Custom": "literal-value",
    })
    assert err == ""
    assert resolved == {"Authorization": "sk-real-secret-value", "X-Custom": "literal-value"}


# ---------------------------------------------------------------------------
# http_probe() -- integration
# ---------------------------------------------------------------------------

def test_http_probe_sends_resolved_plaintext(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.set("target_token", "sk-real-secret-value")
    bridge = _bridge()
    with patch("requests.request", return_value=_mock_response()) as mock_req:
        result = bridge.http_probe(
            "GET", "https://example.com/", actor="planner",
            headers={"Authorization": "vault:target_token"},
        )
    assert result.blocked is not True
    sent_headers = mock_req.call_args.kwargs["headers"]
    assert sent_headers["Authorization"] == "sk-real-secret-value"


def test_http_probe_missing_vault_ref_blocks_before_request(tmp_path, monkeypatch):
    """Reachability-proof: sink nyata (requests.request) TIDAK terpanggil
    kalau vault ref eksplisit tidak bisa di-resolve -- fail-closed, bukan
    diam-diam kirim string 'vault:...' atau 'None' literal ke target."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    bridge = _bridge()
    with patch("requests.request") as mock_req:
        result = bridge.http_probe(
            "GET", "https://example.com/", actor="planner",
            headers={"Authorization": "vault:does_not_exist"},
        )
    assert result.blocked is True
    assert "Cannot resolve" in (result.block_reason or "")
    mock_req.assert_not_called()


def test_resolved_plaintext_never_stored_in_transcript(tmp_path, monkeypatch):
    """Defense-in-depth: nilai plaintext yang di-resolve tidak pernah bocor
    ke TerminalResult (command/stdout/stderr) -- headers memang tidak pernah
    jadi bagian transcript yang tersimpan, dites eksplisit di sini supaya
    tidak jadi asumsi diam-diam."""
    v = _vault(tmp_path, monkeypatch)
    v.set("target_token", "sk-real-secret-value")
    bridge = _bridge()
    with patch("requests.request", return_value=_mock_response()):
        result = bridge.http_probe(
            "GET", "https://example.com/", actor="planner",
            headers={"Authorization": "vault:target_token"},
        )
    assert "sk-real-secret-value" not in result.command
    assert "sk-real-secret-value" not in (result.stdout or "")
    assert "sk-real-secret-value" not in (result.stderr or "")


def test_http_probe_without_headers_unaffected():
    """Compatibility: caller yang tidak pernah mengoper headers sama sekali
    (jalur lama) tidak berubah perilakunya."""
    bridge = _bridge()
    with patch("requests.request", return_value=_mock_response()):
        result = bridge.http_probe("GET", "https://example.com/", actor="planner")
    assert result.blocked is not True


# ---------------------------------------------------------------------------
# dispatch_tool() / broker -- end-to-end wiring proof
# ---------------------------------------------------------------------------

def test_dispatch_tool_threads_vault_headers_through_broker(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.set("target_token", "sk-real-secret-value")
    bridge = _bridge()
    with patch("requests.request", return_value=_mock_response()) as mock_req:
        result = bridge.dispatch_tool(
            "http_probe",
            {"method": "GET", "url": "https://example.com/", "headers": {"Authorization": "vault:target_token"}},
            actor="planner",
        )
    assert result.blocked is not True
    assert mock_req.call_args.kwargs["headers"]["Authorization"] == "sk-real-secret-value"


# ---------------------------------------------------------------------------
# Tool schema -- headers actually discoverable by the LLM
# ---------------------------------------------------------------------------

def test_http_probe_schema_exposes_headers_param():
    props = TOOL_DEFINITIONS["http_probe"]["function"]["parameters"]["properties"]
    assert "headers" in props
