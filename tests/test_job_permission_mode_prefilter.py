"""Regression tests — WU-103: `jobs/policy.py::PolicyEngine.evaluate()` coarse
pre-filter gains a `permission_mode` floor check (PRD-005 FR#4, scope-adjusted).

Scope decision (lihat docstring `wth_ai/jobs/policy.py` untuk rincian penuh):
PRD-005 FR#4 literal ("panggil evaluate_intent() dengan
ActionIntent(tool_name=f'job.{type}')") TIDAK diimplementasikan apa adanya --
`evaluate_intent()` me-resolve `tool` terhadap `PermissionProfile` NYATA
(nama tool eksekutabel asli); token sintetis "job.executor" tidak cocok satu
pun tanpa menambah entry capability palsu ke 5 profile bawaan (scope creep
ke file security-sensitive bersama untuk pseudo-tool). Tujuan yang sama
(tangkap job under-privileged SEBELUM admisi, bukan cuma setelah dequeue)
dicapai lewat `permission_mode` + `PERMISSION_LEVEL` yang SUDAH ada & sudah
teruji -- mirror floor yang sudah nyata ditegakkan `run_executor_job()`
pasca-admisi.

Catatan jujur soal reachability (bukan overclaim): untuk `type="executor"`
lewat `JobManager.create_job()` (satu-satunya caller nyata), gate
`_DESTRUCTIVE_TYPES`/`safe_mode` (WU-008, SELALU aktif di sana -- create_job()
tidak expose parameter `safe_mode` sama sekali) menutup jalur itu LEBIH DULU,
jadi floor `permission_mode` baru ini TIDAK reachable via create_job() untuk
executor hari ini -- ditutupi test compat di bawah, bukan disembunyikan.
Nilai nyata cek ini: `PolicyEngine.evaluate()` sudah punya jalur teruji
`safe_mode=False` (override eksplisit, `test_job_policy_destructive.py`) yang
SEBELUM WU ini bisa lolos meski permission_mode terlalu rendah -- itu bug
nyata yang ditutup, terlepas dari belum adanya caller produksi yang memakai
override tsb.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
from wth_ai.config import WTHConfig
from wth_ai.evidence.vault import EvidenceVault
from wth_ai.findings_db import FindingsDB
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.jobs.manager import JobManager
from wth_ai.jobs.policy import PolicyEngine
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.models import PermissionMode, SessionState
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate() -- permission_mode floor, unit level
# ---------------------------------------------------------------------------

def test_permission_mode_none_preserves_pre_wu103_behavior(monkeypatch):
    """Compatibility kunci: default None (4 caller nyata pre-WU-103 tidak
    mengoper parameter ini sama sekali) -- floor check TIDAK dievaluasi."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False, permission_mode=None,
    )
    assert allowed is True
    assert reason == ""


def test_permission_mode_below_floor_denies_with_safe_mode_override(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False,
        permission_mode=PermissionMode.READ_ONLY_RECON,
    )
    assert allowed is False
    assert "permission mode" in reason


def test_permission_mode_at_floor_allows(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False,
        permission_mode=PermissionMode.STANDARD_TESTING,
    )
    assert allowed is True
    assert reason == ""


def test_permission_mode_above_floor_allows(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False,
        permission_mode=PermissionMode.FULL_LAB,
    )
    assert allowed is True


def test_permission_mode_accepts_string_value(monkeypatch):
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, _ = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False, permission_mode="standard",
    )
    assert allowed is True

    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False, permission_mode="read_only",
    )
    assert allowed is False
    assert "permission mode" in reason


def test_permission_mode_invalid_string_denies_fail_closed(monkeypatch):
    """FAIL-CLOSED LAW: MISSING (None) skip, tapi INVALID (string sampah yang
    eksplisit dioper) harus DENY, bukan diam-diam di-skip juga."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", safe_mode=False, permission_mode="not_a_real_mode",
    )
    assert allowed is False
    assert "unrecognized" in reason


def test_permission_mode_ignored_for_types_without_a_floor(monkeypatch):
    """recon tidak ada di _MIN_PERMISSION_LEVEL -- permission_mode serendah
    apa pun tidak berpengaruh (compat, tidak menambah gate baru untuk recon)."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="recon", target="x.com", permission_mode=PermissionMode.READ_ONLY_RECON,
    )
    assert allowed is True
    assert reason == ""


def test_unrestricted_still_bypasses_permission_mode_floor(monkeypatch):
    """Precedence tidak berubah: is_unrestricted() tetap menang duluan,
    identik dengan bypass safe_mode/_DESTRUCTIVE_TYPES yang sudah ada."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "1")
    try:
        allowed, reason = PolicyEngine(WTHConfig()).evaluate(
            type="executor", target="x.com", permission_mode=PermissionMode.READ_ONLY_RECON,
        )
        assert allowed is True
        assert reason == ""
    finally:
        monkeypatch.setenv("WTH_UNRESTRICTED", "0")


def test_executor_via_create_job_still_shadowed_by_destructive_gate(monkeypatch):
    """Catatan jujur reachability (lihat docstring modul): create_job() tidak
    expose safe_mode, jadi _DESTRUCTIVE_TYPES gate WU-008 menutup 'executor'
    lebih dulu terlepas dari permission_mode berapa pun -- floor baru ini
    TIDAK mengubah reason string end-to-end untuk kombinasi ini."""
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    allowed, reason = PolicyEngine(WTHConfig()).evaluate(
        type="executor", target="x.com", permission_mode=PermissionMode.FULL_LAB,
    )
    assert allowed is False
    assert "destructive" in reason  # bukan "permission mode" -- destructive gate menang duluan


# ---------------------------------------------------------------------------
# JobManager.create_job() -- wiring proof (parameter benar2 diteruskan)
# ---------------------------------------------------------------------------

class _SpyPolicy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        return True, ""


def test_create_job_threads_permission_mode_to_policy(tmp_path, monkeypatch):
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    cfg_mod.USER_CONFIG_PATH = tmp_path / "config" / "user.yaml"
    cfg_mod.USER_ENV_PATH = tmp_path / ".env"
    init_workspace(tmp_path)

    spy = _SpyPolicy()
    mgr = JobManager(WTHConfig(), policy=spy)
    mgr.create_job("recon", target="x.com", permission_mode=PermissionMode.ELEVATED_APPROVAL)
    assert len(spy.calls) == 1
    assert spy.calls[0]["permission_mode"] == PermissionMode.ELEVATED_APPROVAL


def test_create_job_permission_mode_defaults_to_none(tmp_path, monkeypatch):
    """Compat: caller lama yang tidak mengoper permission_mode sama sekali
    tetap mengoper None ke policy -- bukan berubah jadi wajib."""
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    cfg_mod.USER_CONFIG_PATH = tmp_path / "config" / "user.yaml"
    cfg_mod.USER_ENV_PATH = tmp_path / ".env"
    init_workspace(tmp_path)

    spy = _SpyPolicy()
    mgr = JobManager(WTHConfig(), policy=spy)
    mgr.create_job("recon", target="x.com")
    assert spy.calls[0]["permission_mode"] is None


# ---------------------------------------------------------------------------
# supervisor_dispatch.py's job_create_recon tool -- wiring proof
# ---------------------------------------------------------------------------

class _SpyJobManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_job(self, type, **kwargs):
        self.calls.append({"type": type, **kwargs})

        class _Job:
            job_id = "JOB-spy"
            status = "queued"

        return _Job()


def _ctx(tmp_path) -> SupervisorContext:
    config = WTHConfig.load()
    return SupervisorContext(
        config=config,
        client=LLMProviderRouter(config),
        state=SessionState(),
        scope_mgr=ScopeManager(),
        permission_mgr=PermissionManager(),
        asset_graph=AssetGraph("test-job-prefilter", base_dir=tmp_path),
        evidence_vault=EvidenceVault("test-job-prefilter", base_dir=tmp_path),
        findings_db=FindingsDB(),
        terminal=AgentTerminalBridge(config, PermissionManager(), ScopeManager(), "test-job-prefilter"),
    )


def test_job_create_recon_tool_threads_session_permission_mode(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.state.scope.target = "x.com"
    ctx.state.permission_mode = PermissionMode.ELEVATED_APPROVAL
    spy = _SpyJobManager()
    ctx.job_manager = spy

    dispatch_supervisor_tool(ctx, "job_create_recon", {}, approved=False)

    assert len(spy.calls) == 1
    assert spy.calls[0]["permission_mode"] == PermissionMode.ELEVATED_APPROVAL
