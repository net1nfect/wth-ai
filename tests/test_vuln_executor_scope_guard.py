"""Regression tests — WU-012: guard sinkron `execute_module()`/`executor/vuln/*.py`
(AGENT-02, urgensi dinaikkan P1->P0 saat second-pass critique arsitektur).

Bug asli: kelima vuln executor (idor/ssrf/xss/auth/race) memanggil
`requests.get()`/`SessionEngine.request()` LANGSUNG dengan `task.target` --
`task.target` berasal dari planner (LLM-proposed, lihat `execute_module()`
line ~86-87: `if not task.target: task.target = state.scope.target or ""`,
tapi kalau planner ISI eksplisit, nilai itu dipakai apa adanya). Tidak ada
`is_in_scope()` check di jalur ini sama sekali sebelum WU ini -- pelanggaran
langsung prinsip "LLM PROPOSES, POLICY DECIDES": proposal LLM (target) lolos
ke HTTP call nyata tanpa keputusan policy.

Fix: `execute_module()` (titik konvergensi bersama kelima vuln executor)
sekarang memanggil `ScopeManager.is_in_scope(task.target)` SEBELUM
instantiate executor manapun -- satu check menutup celah untuk kelimanya
sekaligus, tidak perlu menyentuh 5 file vuln/*.py individual.

Scope decision: TIDAK mengganti check permission_mode==READ_ONLY_RECON yang
sudah ada dengan `evaluate_intent()` (WU-005) -- vuln module (idor/ssrf/dst)
bukan "shell tool" dalam taksonomi Capability Matrix (argv[0]-based), memaksa
lewat `evaluate_intent()` butuh menciptakan kategori Capability baru yang
tidak berdasar apa pun konkret saat ini (architecture leap). Check
permission_mode existing sudah berperan sebagai gate tier-kapabilitas
informal; `is_in_scope()` adalah SATU-SATUNYA lapisan yang benar-benar hilang
dan dievidence-kan.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.agents.executor import ExecutorAgent
from wth_ai.config import WTHConfig
from wth_ai.models import ExecutorTask, PermissionMode, SessionState


def _executor() -> ExecutorAgent:
    config = WTHConfig.load()
    client = MagicMock()
    tool_runner = MagicMock()
    return ExecutorAgent(config, client, tool_runner)


def _state_with_scope(*, target: str, in_scope: list[str] | None = None, authorized: bool = True) -> SessionState:
    state = SessionState()
    state.permission_mode = PermissionMode.STANDARD_TESTING
    state.scope.target = target
    state.scope.authorized = authorized
    if in_scope is not None:
        state.scope.in_scope = in_scope
    return state


def _task(target: str, vuln_type: str = "ssrf") -> ExecutorTask:
    return ExecutorTask(
        id="T-scope-guard",
        goal="probe",
        module="dynamic",
        target=target,
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": vuln_type},
    )


# ---------------------------------------------------------------------------
# Negative -- target di luar scope, kelima vuln type
# ---------------------------------------------------------------------------

def test_out_of_scope_target_blocked_for_all_vuln_types():
    executor = _executor()
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    for vuln_type in ("idor", "ssrf", "xss", "auth", "race"):
        task = _task("https://evil-external.tld/callback", vuln_type)
        result = executor.execute_module(task, state)
        assert result.status == "blocked", f"{vuln_type} lolos untuk target out-of-scope"
        assert result.errors == "out_of_scope"
        assert "out of scope" in result.output_summary.lower()


def test_out_of_scope_ssrf_never_reaches_requests_get():
    """Bukti reachability: sink nyata (requests.get di ssrf.py) TIDAK
    terpanggil sama sekali untuk target out-of-scope -- bukan cuma reason
    text yang benar, tapi sink HTTP-nya benar-benar tidak tereksekusi."""
    executor = _executor()
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    task = _task("https://attacker-controlled.tld/x?url=http://169.254.169.254/", "ssrf")
    with patch("requests.get") as mock_get:
        result = executor.execute_module(task, state)
    assert result.status == "blocked"
    mock_get.assert_not_called()


def test_no_scope_configured_at_all_blocks_vuln_module():
    """Boundary: scope belum diset sama sekali (fresh SessionState) -- fail-closed."""
    executor = _executor()
    state = SessionState()
    state.permission_mode = PermissionMode.STANDARD_TESTING
    task = _task("https://example.com/x", "ssrf")
    result = executor.execute_module(task, state)
    assert result.status == "blocked"
    assert result.errors == "out_of_scope"


# ---------------------------------------------------------------------------
# Positive/compatibility -- target in-scope tetap diproses seperti sebelumnya
# ---------------------------------------------------------------------------

def test_in_scope_target_still_dispatches_to_vuln_executor():
    """Compatibility: fix ini TIDAK membuat target in-scope ikut ter-block --
    eksekusi tetap lanjut ke executor asli (dibuktikan lewat requests.get
    yang TETAP terpanggil untuk kasus SSRF in-scope)."""
    executor = _executor()
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    task = _task("https://example.com/redirect?url=x", "ssrf")
    fake_resp = MagicMock(status_code=200, text="ok", headers={})
    with patch("requests.get", return_value=fake_resp) as mock_get:
        result = executor.execute_module(task, state)
    assert result.status != "blocked"
    mock_get.assert_called_once()


def test_read_only_recon_block_still_takes_precedence():
    """Compatibility: gate permission_mode existing (READ_ONLY_RECON) tetap
    jalan seperti sebelumnya, TIDAK diganti/dihapus oleh fix ini."""
    executor = _executor()
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    state.permission_mode = PermissionMode.READ_ONLY_RECON
    task = _task("https://example.com/x", "ssrf")
    result = executor.execute_module(task, state)
    assert result.status == "blocked"
    assert result.errors == "read_only"


def test_scope_check_runs_after_permission_check_not_before():
    """Urutan: READ_ONLY_RECON tetap dicek duluan (reason 'read_only', BUKAN
    'out_of_scope') -- urutan gate existing tidak berubah, cuma ditambah satu
    lapis baru setelahnya."""
    executor = _executor()
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    state.permission_mode = PermissionMode.READ_ONLY_RECON
    task = _task("https://totally-unrelated-out-of-scope.tld/x", "ssrf")
    result = executor.execute_module(task, state)
    assert result.errors == "read_only"  # bukan "out_of_scope"


def test_non_vuln_module_task_unaffected_by_scope_guard():
    """Compatibility: task non-vuln yang targetnya IN-SCOPE tidak ikut ter-block
    oleh gate mana pun -- jalur manifest-runner/tool-based existing tetap jalan
    sampai titik kegagalannya sendiri (di sini: manifest runner unavailable).

    Catatan revisi (review pre-merge): sebelumnya test ini memakai target
    OUT-OF-SCOPE (`https://out-of-scope-but-irrelevant.tld/x`) dan menuntut
    `errors != "out_of_scope"`. Itu benar sebagai batas scope WU-012 (yang
    sengaja hanya menutup jalur vuln-executor), TAPI mengunci sebuah celah:
    jalur module non-vuln menjalankan `task.command_plan` lewat
    ExecutorRunnerV2 tanpa gate permission/scope/policy apa pun. Celah itu kini
    ditutup -- lihat test di bawah -- jadi assertion-nya dipindah ke target
    in-scope, yang memang intent aslinya ("tidak tersentuh" untuk pemakaian
    normal).
    """
    executor = _executor()
    executor.manifest_runner = None  # paksa jalur "Manifest runner unavailable"
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    task = ExecutorTask(
        id="T-not-vuln", goal="scan", module="dynamic",
        target="https://example.com/x",
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": "recon"},  # bukan salah satu VULN_EXECUTORS key
    )
    result = executor.execute_module(task, state)
    assert result.errors != "out_of_scope"


def test_non_vuln_module_task_out_of_scope_is_now_blocked():
    """Review pre-merge (A1/P0): the non-vuln module path reaches subprocess via
    ExecutorRunnerV2, which has no permission/scope/policy dependency of its
    own. Before the gate it executed `task.command_plan` verbatim for any
    target, in any permission mode -- `uname -a` and `python3 -c print(42)`
    both ran in READ_ONLY mode even though `python3` is REQUIRE_HUMAN
    everywhere. Scope is now enforced here too."""
    executor = _executor()
    executor.manifest_runner = None
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    task = ExecutorTask(
        id="T-not-vuln-oos", goal="scan", module="dynamic",
        target="https://out-of-scope.tld/x",
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": "recon"},
        command_plan=[{"command": "uname -a", "purpose": "probe"}],
    )
    result = executor.execute_module(task, state)
    assert result.status == "blocked"
    assert result.errors == "out_of_scope"


def test_non_vuln_module_task_insufficient_permission_is_blocked():
    executor = _executor()
    executor.manifest_runner = None
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    state.permission_mode = PermissionMode.READ_ONLY_RECON
    task = ExecutorTask(
        id="T-not-vuln-perm", goal="scan", module="dynamic",
        target="https://example.com/x",
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": "recon"},
    )
    result = executor.execute_module(task, state)
    assert result.status == "blocked"
    assert result.errors == "insufficient_permission"


def test_non_vuln_module_command_plan_is_policy_checked():
    """Per-command gate: the module runs argv strings, so they go through
    check_command() like every other route to subprocess."""
    executor = _executor()
    executor.manifest_runner = None
    state = _state_with_scope(target="example.com", in_scope=["example.com"])
    task = ExecutorTask(
        id="T-not-vuln-cmd", goal="scan", module="dynamic",
        target="https://example.com/x",
        permission_required=PermissionMode.STANDARD_TESTING,
        evidence_schema={"type": "recon"},
        command_plan=[{"command": "python3 -c print(42)", "purpose": "probe"}],
    )
    result = executor.execute_module(task, state)
    assert result.status == "blocked"
    assert "blocked by policy" in result.output_summary.lower()
