"""Regression tests — WU-102: `ExecutionBroker` single execution chokepoint
(PRD-011, ADR-008).

Scope decision (P0 slice dari priority split PRD-011 sendiri: "P0 (skeleton
+ AgentTerminalBridge facade) / P1 (integrasi penuh 5 jalur eksekusi)"):
`ExecutionBroker.execute()` WRAPS `AgentTerminalBridge.run_shell()`/
`http_probe()` (composition, reuse logic scope/permission/denylist/approval/
throttle APA ADANYA) -- BUKAN reimplementasi 8-stage pipeline target
arsitektur. 6 dari 9 stage yang disebut PRD-011 (actor/trust resolution
context.tainted, Engagement fetch-fresh, Approval Gate action-hashing,
sandbox+RunningActionRegistry, audit sinkron, evidence auto-store) BELUM
diimplementasikan -- semua butuh infrastruktur yang belum ada (WU-007
remainder, WU-101, WU-105, WU-106, WU-114/115). Migrasi 4 jalur eksekusi
lain (ExecuteRpcServer, executor/vuln/*.py, WTHOrchestrator._execute_next,
ReconEngine) ke broker yang sama JUGA belum dikerjakan -- itu WU-104+.

`dispatch_tool()` (satu-satunya call site yang direfactor) HARUS tetap
identik secara observable -- test di file ini membuktikan itu secara
eksplisit, bukan cuma "tidak crash".
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.broker.broker import ExecutionBroker
from wth_ai.broker.models import ActionIntent
from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge


def _bridge(mode: PermissionMode = PermissionMode.FULL_LAB) -> AgentTerminalBridge:
    config = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    return AgentTerminalBridge(config, PermissionManager(mode), scope, "test-broker")


# ---------------------------------------------------------------------------
# ExecutionBroker.execute() -- unit level
# ---------------------------------------------------------------------------

def test_broker_routes_run_shell():
    bridge = _bridge()
    broker = ExecutionBroker(bridge)
    intent = ActionIntent(intent_id="AI-1", tool_name="run_shell", arguments={"command": "cat /etc/hostname"}, actor="executor")
    result = broker.execute(intent)
    assert result.intent_id == "AI-1"
    assert result.blocked is not True


def test_broker_routes_http_probe():
    bridge = _bridge()
    broker = ExecutionBroker(bridge)
    intent = ActionIntent(intent_id="AI-2", tool_name="http_probe", arguments={"method": "GET", "url": "https://example.com/"}, actor="planner")
    result = broker.execute(intent)
    assert result.intent_id == "AI-2"
    assert result.method == "GET"
    assert result.url == "https://example.com/"


def test_broker_unknown_tool_blocked_with_exact_legacy_purpose():
    """Bukti kompatibilitas presisi: dispatch_tool() lama memanggil
    self._blocked(tid, actor, name, str(arguments), ...) -- purpose=name
    (bukan arguments.get("purpose","")). Broker harus reproduksi ini persis."""
    bridge = _bridge()
    broker = ExecutionBroker(bridge)
    intent = ActionIntent(
        intent_id="AI-3", tool_name="totally_unknown_tool",
        arguments={"purpose": "should NOT be used"}, actor="executor",
    )
    result = broker.execute(intent)
    assert result.blocked is True
    assert result.purpose == "totally_unknown_tool"
    assert "Unknown tool" in result.block_reason


def test_execution_result_fields_lossless_roundtrip():
    bridge = _bridge()
    broker = ExecutionBroker(bridge)
    intent = ActionIntent(intent_id="AI-4", tool_name="run_shell", arguments={"command": "echo hi", "purpose": "test"}, actor="executor")
    result = broker.execute(intent)
    for attr in ("transcript_id", "actor", "purpose", "command", "stdout", "stderr", "exit_code", "blocked", "block_reason", "url", "method", "status_code"):
        assert hasattr(result, attr)


# ---------------------------------------------------------------------------
# dispatch_tool() -- compatibility end-to-end (facade transparansi)
# ---------------------------------------------------------------------------

def test_dispatch_tool_run_shell_unchanged_behavior():
    bridge = _bridge()
    result = bridge.dispatch_tool("run_shell", {"command": "cat /etc/hostname"}, actor="executor", approved=True)
    assert result.blocked is not True


def test_dispatch_tool_http_probe_still_gated_for_post():
    """Reproduksi eksplisit WU-007's http_probe approval gate -- masih
    berfungsi identik lewat facade broker baru."""
    bridge = _bridge()
    blocked = bridge.dispatch_tool("http_probe", {"method": "POST", "url": "https://example.com/x"}, actor="planner", approved=False)
    assert blocked.blocked is True
    allowed = bridge.dispatch_tool("http_probe", {"method": "POST", "url": "https://example.com/x"}, actor="planner", approved=True)
    assert allowed.blocked is not True


def test_dispatch_tool_http_probe_head_reproduces_existing_test_wth_ai_case():
    """Reproduksi persis tests/test_wth_ai.py::test_terminal_bridge_http_probe,
    tapi lewat dispatch_tool() bukan http_probe() langsung."""
    bridge = _bridge()
    result = bridge.dispatch_tool(
        "http_probe", {"method": "HEAD", "url": "https://example.com"}, actor="planner",
    )
    assert result.transcript_id.startswith("TX-")
    assert bridge.transcripts.count() >= 1


def test_dispatch_tool_unknown_tool_unchanged():
    bridge = _bridge()
    result = bridge.dispatch_tool("nonexistent_tool_xyz", {}, actor="executor")
    assert result.blocked is True
    assert "Unknown tool" in result.block_reason


def test_dispatch_tool_scope_and_permission_gates_still_enforced_through_facade():
    """Compatibility kunci: gate scope/permission (bukan cuma approval) tetap
    berfungsi lewat broker -- bukan cuma jalur happy-path yang teruji."""
    bridge = _bridge(PermissionMode.READ_ONLY_RECON)
    result = bridge.dispatch_tool("run_shell", {"command": "bash -c id"}, actor="executor")
    assert result.blocked is True

    bridge2 = _bridge()
    result2 = bridge2.dispatch_tool("http_probe", {"method": "GET", "url": "https://out-of-scope-xyz.tld/"}, actor="planner")
    assert result2.blocked is True


def test_broker_never_double_stores_transcript():
    """run_shell()/http_probe() sudah menyimpan transcript sendiri --
    broker TIDAK boleh menyimpan lagi (hindari duplikasi count)."""
    bridge = _bridge()
    before = bridge.transcripts.count()
    bridge.dispatch_tool("run_shell", {"command": "echo hi"}, actor="executor", approved=True)
    after = bridge.transcripts.count()
    assert after == before + 1
