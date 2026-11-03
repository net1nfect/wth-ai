"""Regression tests — P0 Exit Review finding (HIGH): `approved=True` sekarang
benar-benar bisa unblock keputusan REQUIRE_HUMAN/high-risk-pattern.

Bug asli (ditemukan oleh adversarial exit-review subagent, TERKONFIRMASI
lewat reproduksi mandiri sebelum fix ditulis): `evaluate_intent()`
mengembalikan `allowed=False` TANPA SYARAT untuk kapabilitas
`REQUIRE_HUMAN` (mis. `shell.interpreter_passthrough` -- bash/sh/zsh/python/
dst, WU-005) dan untuk `HIGH_RISK_PATTERNS` match (Action-Policy layer) --
`evaluate_intent()`/`check_command()` bahkan TIDAK PUNYA parameter `approved`
sama sekali. Kedua caller live (`terminal/bridge.py::run_shell()`,
`tool_runner.py::execute_task()`) mengecek `if not check["allowed"]: return
blocked` LEBIH DULU, sebelum blok kedua mereka sendiri
(`if approval_needed and not approved`) -- yang berarti blok kedua itu
TIDAK PERNAH tercapai, `approved=True` tidak berefek apa pun.

Bug ini PRE-EXISTING (ada sebelum WU-005 juga, dengan bentuk hand-rolled
yang sama di check_command() lama), tapi WU-005 menulis ulang & menguji
ekstensif fungsi ini (20 test) tanpa menangkapnya -- exit-review menemukan
dan melaporkannya, sekarang diperbaiki.

Fix: `evaluate_intent()`/`check_command()`/`run_shell()`/`execute_task()`
semuanya menerima & menghormati `approved` secara konsisten -- `approved=True`
melewati gate REQUIRE_HUMAN maupun HIGH_RISK_PATTERNS, `approval_needed` di
hasil tetap True (informational/audit), TIDAK mempengaruhi `allowed`.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.policy.engine import evaluate_intent
from wth_ai.policy.profiles_builtin import PROFILES
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge


# ---------------------------------------------------------------------------
# Unit level -- evaluate_intent()
# ---------------------------------------------------------------------------

def test_require_human_capability_unblocked_when_approved():
    profile = PROFILES[PermissionMode.FULL_LAB]
    decision = evaluate_intent(command="bash -c 'id'", profile=profile, approved=True)
    assert decision.allowed is True
    assert decision.approval_needed is True  # informational -- tetap tercatat butuh approval


def test_require_human_capability_still_blocked_when_not_approved():
    """Compatibility: default approved=False tetap deny seperti sebelumnya."""
    profile = PROFILES[PermissionMode.FULL_LAB]
    decision = evaluate_intent(command="bash -c 'id'", profile=profile, approved=False)
    assert decision.allowed is False
    decision_default = evaluate_intent(command="bash -c 'id'", profile=profile)
    assert decision_default.allowed is False


def test_high_risk_pattern_unblocked_when_approved():
    profile = PROFILES[PermissionMode.UNRESTRICTED]
    decision = evaluate_intent(
        command="hydra -l admin -P wordlist.txt target.com", profile=profile, approved=True,
    )
    assert decision.allowed is True


def test_high_risk_pattern_still_blocked_when_not_approved():
    profile = PROFILES[PermissionMode.UNRESTRICTED]
    decision = evaluate_intent(
        command="hydra -l admin -P wordlist.txt target.com", profile=profile, approved=False,
    )
    assert decision.allowed is False


def test_denylist_never_unblocked_even_when_approved():
    """Denylist absolut TIDAK PERNAH punya bypass approval -- beda dari
    REQUIRE_HUMAN/high-risk-pattern, ini benar-benar tidak boleh punya jalan keluar."""
    profile = PROFILES[PermissionMode.UNRESTRICTED]
    decision = evaluate_intent(command="rm -rf /", profile=profile, approved=True)
    assert decision.allowed is False


# ---------------------------------------------------------------------------
# check_command() adapter -- threading approved
# ---------------------------------------------------------------------------

def test_check_command_threads_approved_through():
    pm = PermissionManager(PermissionMode.FULL_LAB)
    blocked = pm.check_command("bash -c 'id'")
    assert blocked["allowed"] is False
    allowed = pm.check_command("bash -c 'id'", approved=True)
    assert allowed["allowed"] is True


# ---------------------------------------------------------------------------
# End-to-end -- run_shell() (caller live #1)
# ---------------------------------------------------------------------------

def _bridge(mode: PermissionMode = PermissionMode.FULL_LAB) -> AgentTerminalBridge:
    config = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    return AgentTerminalBridge(config, PermissionManager(mode), scope, "test-approval-unblock")


def test_run_shell_interpreter_unblocked_when_approved():
    """REGRESI-GUARD utama: reproduksi persis skenario yang ditemukan
    exit-review -- run_shell() adalah jalur eksekusi shell utama agent
    (dipanggil dari AgentToolLoop via ExecutorAgent._execute_with_tools)."""
    bridge = _bridge()
    result = bridge.run_shell("bash -c 'echo hi'", actor="executor", approved=True)
    assert result.blocked is not True
    assert "hi" in result.stdout


def test_run_shell_interpreter_still_blocked_when_not_approved():
    bridge = _bridge()
    result = bridge.run_shell("bash -c 'echo hi'", actor="executor", approved=False)
    assert result.blocked is True
    assert "approval" in (result.block_reason or "").lower()


# ---------------------------------------------------------------------------
# End-to-end -- ToolRunner.execute_task() (caller live #2)
# ---------------------------------------------------------------------------

def test_execute_task_check_command_call_receives_approved():
    """ToolRunner.execute_task() memanggil check_command(..., approved=approved)
    -- verifikasi langsung terhadap source (bukan lewat eksekusi subprocess
    penuh, supaya test tidak butuh binary asli terinstal)."""
    import inspect

    from wth_ai.tool_runner import ToolRunner

    src = inspect.getsource(ToolRunner.execute_task)
    assert "approved=approved" in src, (
        "REGRESI: ToolRunner.execute_task() tidak lagi mengoper approved ke check_command()"
    )
