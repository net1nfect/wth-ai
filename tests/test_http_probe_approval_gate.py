"""Regression tests — WU-007 (subset): `http_probe` approval gate (PRD-006).

Bug asli: `AgentTerminalBridge.http_probe()` TIDAK PUNYA parameter `approved`
sama sekali (beda dari `run_shell()` yang punya sejak awal) -- tidak ada
gate approval untuk HTTP request LLM-driven, bahkan secara teori. Diverifikasi
ulang manual sebelum implementasi (`terminal/bridge.py`): `run_shell` baris
~84 sudah punya `approved: bool = False` + cek `check_command()`/
`approval_needed`; `http_probe` (sebelum fix ini) langsung ke
`requests.request()` setelah cek scope+permission-mode saja.

Fix: `http_probe()` sekarang punya parameter `approved` + gate risiko:
- method mutating (POST/PUT/DELETE/PATCH) SELALU butuh approval (HTTP
  semantics standar -- GET/HEAD/OPTIONS idempotent/read-only tidak).
- Konten (method+url+body) yang match DENYLIST_PATTERNS/HIGH_RISK_PATTERNS
  (konstanta sama yang dipakai run_shell/evaluate_intent/ExecuteRpcServer)
  JUGA memicu approval_needed apa pun method-nya.
`dispatch_tool()` (titik konvergensi run_shell+http_probe) sekarang
mengoper `approved` (sudah ada di signature-nya, sebelumnya cuma dipakai
run_shell) DAN `body` (sebelumnya tidak dioper sama sekali ke http_probe)
ke http_probe juga.

Scope decision (WORK-UNITS.md WU-007): HANYA gap http_probe yang
diimplementasikan di sini -- unifikasi 4 mekanisme approval paralel
(SupervisorContext.approved_tasks/WTHOrchestrator._approved_tasks/
ApprovalGate/ApprovalRegistry), action-hashing kanonik, dan
`context.tainted` (prompt-injection isolation struktural) TIDAK disentuh --
masing-masing perubahan besar tersendiri yang butuh desain terpisah,
dicatat sebagai sisa WU-007 yang belum dikerjakan.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge


def _bridge() -> AgentTerminalBridge:
    config = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    return AgentTerminalBridge(config, PermissionManager(), scope, "test-http-approval")


# ---------------------------------------------------------------------------
# Negative -- method mutating butuh approval
# ---------------------------------------------------------------------------

def test_post_without_approval_blocked():
    bridge = _bridge()
    result = bridge.http_probe("POST", "https://example.com/api/users", actor="planner")
    assert result.blocked is True
    assert "approval" in (result.block_reason or "").lower()


def test_put_delete_patch_without_approval_all_blocked():
    bridge = _bridge()
    for method in ("PUT", "DELETE", "PATCH"):
        result = bridge.http_probe(method, "https://example.com/api/x", actor="planner")
        assert result.blocked is True, f"{method} lolos tanpa approval"


def test_mutating_method_never_reaches_requests_call():
    """Bukti reachability: sink nyata (requests.request) TIDAK terpanggil
    untuk method mutating tanpa approval."""
    from unittest.mock import patch

    bridge = _bridge()
    with patch("requests.request") as mock_req:
        result = bridge.http_probe("POST", "https://example.com/api/x", actor="planner")
    assert result.blocked is True
    mock_req.assert_not_called()


def test_denylist_pattern_in_url_blocked_even_for_get():
    """Denylist absolut berlaku apa pun method-nya, bahkan GET."""
    bridge = _bridge()
    result = bridge.http_probe("GET", "https://example.com/?cmd=rm -rf /", actor="planner")
    assert result.blocked is True
    assert "denylist" in (result.block_reason or "").lower()


# ---------------------------------------------------------------------------
# Positive/compatibility -- GET/HEAD/OPTIONS tetap tidak butuh approval
# ---------------------------------------------------------------------------

def test_get_still_works_without_approval():
    bridge = _bridge()
    result = bridge.http_probe("GET", "https://example.com/", actor="planner")
    assert result.blocked is not True


def test_head_still_works_without_approval():
    """Compatibility langsung dengan tests/test_wth_ai.py::test_terminal_bridge_http_probe."""
    bridge = _bridge()
    result = bridge.http_probe("HEAD", "https://example.com", actor="planner", purpose="test")
    assert result.blocked is not True
    assert result.transcript_id.startswith("TX-")


def test_post_with_approved_true_proceeds():
    """Positive: kapabilitas mutating tidak dihapus, cuma butuh approval eksplisit."""
    bridge = _bridge()
    result = bridge.http_probe("POST", "https://example.com/api/x", actor="planner", approved=True)
    assert result.blocked is not True


# ---------------------------------------------------------------------------
# dispatch_tool() -- titik konvergensi, approved+body threading
# ---------------------------------------------------------------------------

def test_dispatch_tool_threads_approved_to_http_probe():
    bridge = _bridge()
    blocked = bridge.dispatch_tool(
        "http_probe", {"method": "POST", "url": "https://example.com/api/x"}, actor="planner", approved=False,
    )
    assert blocked.blocked is True

    allowed = bridge.dispatch_tool(
        "http_probe", {"method": "POST", "url": "https://example.com/api/x"}, actor="planner", approved=True,
    )
    assert allowed.blocked is not True
