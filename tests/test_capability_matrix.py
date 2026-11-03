"""Regression tests — WU-005: Capability Matrix + `PermissionProfile` +
`evaluate_intent()` (Policy Engine core, ADR-009/ADR-010).

Scope aktual (lebih sempit dari draft aspirasional PRD-005 -- lihat "Scope
decision" di WORK-UNITS.md untuk alasan lengkap):
1. `wth_ai/policy/capability.py` -- `Capability`/`PermissionProfile` dataclass.
2. `wth_ai/policy/profiles_builtin.py` -- 5 profile built-in, 1:1 mapping dari
   tool set existing di `permission_manager.py` (tidak ada tool yang
   hilang/berubah keanggotaan), plus `shell.interpreter_passthrough` baru.
3. `wth_ai/policy/engine.py::evaluate_intent()` -- core policy engine, 2 dari
   6 layer ADR-010 diimplementasikan nyata (Global-Safety, Tool-Policy,
   Action-Policy); sisanya (Kill-switch/Environment/Engagement/
   Agent-Capability) didokumentasikan eksplisit sebagai deferred (butuh
   infrastruktur yang belum ada -- WU-101/WU-106/WU-007).
4. `PermissionManager.check_command()` -- direfaktor jadi adapter tipis,
   return-shape dict PERSIS sama.

TIDAK diimplementasikan di WU ini (deferred, didokumentasikan eksplisit):
- `jobs/policy.py` integrasi ke `evaluate_intent()` (PRD-005 FR#4) -- itu
  WU-103, depends WU-102 (ExecutionBroker) yang belum ada. WU-008 (sudah
  DONE) sudah benar sebagai coarse pre-filter sesuai ADR-010 §Why.
- UNRESTRICTED wildcard PROHIBITED-unless-Engagement (ADR-009 penuh) --
  butuh `Engagement.allowed_operations` (WU-101), belum ada. Interim:
  wildcard tetap AUTO untuk tool yang genuinely tidak dikatalogkan.

Behavior change YANG DISENGAJA (satu-satunya, dari trace source + impact
analysis eksplisit sebelum implementasi):
- Interpreter (bash/sh/zsh/python/python3/perl/ruby/node) sekarang SELALU
  requires human approval, TERMASUK di mode UNRESTRICTED (sebelumnya
  UNRESTRICTED early-return sebelum tool-allowlist check apa pun dijalankan
  -- auto-bypass total). Tidak ada test existing yang covers kasus ini
  (bash-di-unrestricted) sebelumnya -- zero blast radius terhadap assertion
  lama.
- HIGH_RISK_PATTERNS (Action-Policy) sekarang berlaku juga di UNRESTRICTED
  (sebelumnya di-skip oleh early-return yang sama) -- 1 test existing
  (`test_permission_manager_correctness.py::
  test_unrestricted_no_longer_bypasses_high_risk_action_patterns`, semula
  `test_unrestricted_still_allows_non_denylist_high_risk_commands`) diupdate
  sesuai, sudah ditandai placeholder sementara sejak WU-004.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.models import PermissionMode
from wth_ai.permission_manager import (
    ELEVATED_TOOLS,
    FULL_LAB_TOOLS,
    PermissionManager,
    READ_ONLY_TOOLS,
    STANDARD_TOOLS,
)
from wth_ai.policy.capability import ApprovalMode, Capability, PermissionProfile, RiskLevel
from wth_ai.policy.engine import evaluate_intent
from wth_ai.policy.profiles_builtin import INTERPRETER_TOOLS, PROFILES


# ---------------------------------------------------------------------------
# PermissionProfile.resolve() -- unit level, ordering = precedence
# ---------------------------------------------------------------------------

def test_resolve_returns_none_for_ungranted_tool():
    profile = PermissionProfile("empty", ())
    assert profile.resolve("curl") is None


def test_resolve_first_match_wins_ordering():
    low = Capability("a", frozenset({"curl"}), RiskLevel.LOW, ApprovalMode.AUTO)
    high = Capability("b", frozenset({"curl"}), RiskLevel.HIGH, ApprovalMode.REQUIRE_HUMAN)
    profile = PermissionProfile("test", (low, high))
    assert profile.resolve("curl") is low  # yang pertama menang, bukan yang terakhir


def test_resolve_wildcard_matches_anything():
    wildcard = Capability("wild", frozenset({"*"}), RiskLevel.HIGH, ApprovalMode.AUTO)
    profile = PermissionProfile("test", (wildcard,))
    assert profile.resolve("literally-anything") is wildcard


# ---------------------------------------------------------------------------
# profiles_builtin -- 1:1 mapping dari tool set existing (no tool lost)
# ---------------------------------------------------------------------------

def test_all_five_modes_have_a_profile():
    for mode in PermissionMode:
        assert mode in PROFILES, f"mode {mode} tidak punya PermissionProfile"


def test_read_only_tools_all_resolve_in_read_only_profile():
    profile = PROFILES[PermissionMode.READ_ONLY_RECON]
    for tool in READ_ONLY_TOOLS - INTERPRETER_TOOLS:
        assert profile.resolve(tool) is not None, f"tool {tool!r} hilang dari profile read_only"


def test_standard_tools_all_resolve_in_standard_profile():
    profile = PROFILES[PermissionMode.STANDARD_TESTING]
    for tool in STANDARD_TOOLS - INTERPRETER_TOOLS:
        assert profile.resolve(tool) is not None, f"tool {tool!r} hilang dari profile standard"


def test_elevated_tools_all_resolve_in_elevated_profile():
    profile = PROFILES[PermissionMode.ELEVATED_APPROVAL]
    for tool in ELEVATED_TOOLS - INTERPRETER_TOOLS:
        assert profile.resolve(tool) is not None, f"tool {tool!r} hilang dari profile elevated"


def test_full_lab_tools_all_resolve_in_full_lab_profile():
    profile = PROFILES[PermissionMode.FULL_LAB]
    for tool in FULL_LAB_TOOLS - INTERPRETER_TOOLS:
        assert profile.resolve(tool) is not None, f"tool {tool!r} hilang dari profile full_lab"


def test_tool_above_ceiling_not_granted_in_lower_profile():
    """Compatibility: nmap (standard-tier) TIDAK boleh resolve di profile read_only."""
    profile = PROFILES[PermissionMode.READ_ONLY_RECON]
    assert profile.resolve("nmap") is None


# ---------------------------------------------------------------------------
# evaluate_intent() -- unit level
# ---------------------------------------------------------------------------

def test_evaluate_intent_denylist_before_capability():
    profile = PROFILES[PermissionMode.UNRESTRICTED]
    decision = evaluate_intent(command="rm -rf /", profile=profile)
    assert decision.allowed is False
    assert "denylist" in decision.reason.lower()
    assert decision.risk == "critical"


def test_evaluate_intent_unknown_tool_denied():
    profile = PROFILES[PermissionMode.READ_ONLY_RECON]
    decision = evaluate_intent(command="totally-unrelated-tool target.com", profile=profile)
    assert decision.allowed is False


def test_evaluate_intent_empty_command_allowed_noop():
    profile = PROFILES[PermissionMode.READ_ONLY_RECON]
    decision = evaluate_intent(command="", profile=profile)
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# Interpreter passthrough -- behavior BARU, closes UNRESTRICTED auto-bypass
# ---------------------------------------------------------------------------

def test_interpreter_requires_approval_even_in_unrestricted():
    """REGRESI-GUARD utama WU-005 (ADR-009): sebelumnya UNRESTRICTED
    early-return sebelum tool-allowlist check apa pun -- bash lolos total
    tanpa approval. Sekarang interpreter SELALU governed oleh
    shell.interpreter_passthrough (REQUIRE_HUMAN), termasuk di UNRESTRICTED."""
    pm = PermissionManager(PermissionMode.UNRESTRICTED)
    for interpreter in ("bash", "sh", "zsh", "python", "python3", "perl", "ruby", "node"):
        result = pm.check_command(f"{interpreter} -c 'id'")
        assert result["allowed"] is False, f"{interpreter} lolos di UNRESTRICTED tanpa approval"
        assert result["approval_needed"] is True
        assert "interpreter_passthrough" in result["reason"]


def test_interpreter_requires_approval_in_full_lab_too():
    """Compatibility: bash SUDAH ditolak di full_lab sebelum WU-005 juga
    (tidak pernah ada di FULL_LAB_TOOLS) -- pastikan reason sekarang lebih
    presisi (interpreter_passthrough) tapi allowed tetap False, tidak berubah."""
    pm = PermissionManager(PermissionMode.FULL_LAB)
    result = pm.check_command("bash -c 'id'")
    assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Compatibility -- perilaku existing yang TIDAK berubah oleh WU-005
# ---------------------------------------------------------------------------

def test_sqlmap_still_allowed_in_full_lab_no_new_approval_gate():
    """sqlmap (elevated-tier, sudah ada di FULL_LAB_TOOLS) TETAP auto-allow
    di mode aslinya -- WU-005 TIDAK membuat setiap tool elevated/full_lab
    butuh approval baru, hanya interpreter + action-pattern (lihat docstring
    modul untuk alasan desain)."""
    pm = PermissionManager(PermissionMode.FULL_LAB)
    result = pm.check_command("sqlmap -u http://example.com --batch")
    assert result["allowed"] is True
    assert result["approval_needed"] is False


def test_sqlmap_still_allowed_in_unrestricted():
    """sqlmap di UNRESTRICTED juga tetap allowed (tidak match HIGH_RISK_PATTERNS
    literal, beda dari kasus hydra) -- reproduksi eksplisit
    tests/test_engagement_mode.py::test_unrestricted_bypasses_command_check
    untuk membuktikan WU-005 tidak diam-diam merusaknya."""
    pm = PermissionManager(PermissionMode.UNRESTRICTED)
    result = pm.check_command("sqlmap -u http://example.com --batch")
    assert result["allowed"] is True


def test_standard_tools_still_auto_allowed_no_new_gate():
    pm = PermissionManager(PermissionMode.STANDARD_TESTING)
    for cmd in ("nmap -sV example.com", "ffuf -u http://x/FUZZ", "gobuster dir -u http://x"):
        result = pm.check_command(cmd)
        assert result["allowed"] is True, f"{cmd!r} seharusnya tetap allowed"


def test_denylist_and_argv_exact_match_unchanged_through_adapter():
    """Regression guard: 2 dari 3 bug WU-004 (denylist-before-mode,
    argv-exact-match) tetap tertutup lewat adapter baru -- bukan cuma
    reproduksi test_permission_manager_correctness.py, tapi bukti independen
    lewat jalur evaluate_intent() langsung."""
    pm = PermissionManager(PermissionMode.UNRESTRICTED)
    assert pm.check_command("rm -rf /")["allowed"] is False
    pm2 = PermissionManager(PermissionMode.READ_ONLY_RECON)
    assert pm2.check_command("curl-malicious http://evil.com")["allowed"] is False
    assert pm2.check_command("curl https://example.com")["allowed"] is True


def test_check_command_return_shape_unchanged():
    """PRD-005 FR#3: return-shape dict PERSIS sama dengan sebelum WU-005."""
    pm = PermissionManager(PermissionMode.READ_ONLY_RECON)
    result = pm.check_command("curl https://example.com")
    assert set(result.keys()) == {"command", "current_mode", "allowed", "reason", "risk", "approval_needed"}


def test_required_mode_insufficient_still_denies():
    pm = PermissionManager(PermissionMode.READ_ONLY_RECON)
    result = pm.check_command("curl https://example.com", required_mode=PermissionMode.FULL_LAB)
    assert result["allowed"] is False
    assert "Insufficient permission" in result["reason"]
