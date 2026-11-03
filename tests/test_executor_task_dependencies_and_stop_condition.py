"""Regression tests — WU-117: `ExecutorTask` field baru (`priority`/
`depends_on`/`origin`) + wiring `stop_condition` (PRD-009, ADR-002).

Scope (lebih sempit dari draft PRD-009 -- lihat WORK-UNITS.md WU-117):
- FR#1 (field baru, aditif): DONE.
- FR#2 (advisory sort): DONE, tapi HANYA diterapkan di SATU titik dequeue
  (`supervisor_dispatch.py`'s `execute_task` tool, jalur auto-select tanpa
  `task_id` eksplisit) -- 3 titik lain (`orchestrator.py:776`,
  `engagement_loop.py:107`, `session_store.py:90`) BELUM disentuh, dicatat
  sebagai follow-up.
- FR#3 (wire stop_condition): DONE, TAPI ditemukan sebelum implementasi
  bahwa `ToolRunner.execute_task()` tidak pernah menyimpan evidence ke
  `EvidenceVault` sama sekali (gap terpisah, tertutup sekalian) -- tanpa itu,
  `validate_schema()` akan SELALU gagal (tidak ada apa pun tersimpan untuk
  `finding_id=task.id`), membuat wiring stop_condition jadi dead code kalau
  cuma ditambahkan mentah begitu saja.
- FR#4 (ClaimStore) dan FR#5 (job system hardcode -- PRD-009 sendiri bilang
  "TETAP hardcode untuk sekarang, bukan bug") TIDAK dikerjakan/tidak perlu
  dikerjakan.
- Task graph DAG, topological sort, deteksi siklus: SENGAJA tidak dibangun
  (ADR-002) -- `sort_tasks_by_dependencies()` murni advisory, no-op aman
  kalau ada siklus (task-task yang saling depends_on tidak pernah "ready",
  cuma didorong ke belakang terus, tidak infinite-loop/crash).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.evidence.vault import EvidenceVault
from wth_ai.models import ExecutorTask, PermissionMode, sort_tasks_by_dependencies
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.tool_runner import ToolRunner


# ---------------------------------------------------------------------------
# ExecutorTask -- field baru, backward-compat from_dict()
# ---------------------------------------------------------------------------

def test_new_fields_have_safe_defaults():
    task = ExecutorTask(id="T-1", goal="g", target="x.com", permission_required=PermissionMode.STANDARD_TESTING)
    assert task.priority == 5
    assert task.depends_on == []
    assert task.origin == "llm_json"


def test_from_dict_backward_compatible_with_old_session_data():
    """Acceptance criterion literal PRD-009: data sesi LAMA (tanpa field
    baru) tetap berhasil load tanpa error."""
    old_data = {
        "id": "T-old", "goal": "legacy task", "target": "example.com",
        "permission_required": "standard",
    }
    task = ExecutorTask.from_dict(old_data)
    assert task.priority == 5
    assert task.depends_on == []
    assert task.origin == "llm_json"


def test_from_dict_preserves_explicit_new_fields():
    data = {
        "id": "T-2", "goal": "g", "target": "x.com", "permission_required": "standard",
        "priority": 1, "depends_on": ["T-1"], "origin": "chain_planner",
    }
    task = ExecutorTask.from_dict(data)
    assert task.priority == 1
    assert task.depends_on == ["T-1"]
    assert task.origin == "chain_planner"


# ---------------------------------------------------------------------------
# sort_tasks_by_dependencies() -- unit level
# ---------------------------------------------------------------------------

def test_task_with_unmet_dependency_pushed_to_back():
    """Acceptance criterion literal PRD-009: task dengan depends_on diambil
    SETELAH dependency-nya completed, meski masuk antrian lebih dulu."""
    queue = [
        {"id": "T-2", "depends_on": ["T-1"]},
        {"id": "T-1", "depends_on": []},
    ]
    sorted_queue = sort_tasks_by_dependencies(queue, completed_task_ids=set())
    assert [t["id"] for t in sorted_queue] == ["T-1", "T-2"]


def test_task_ready_once_dependency_completed():
    queue = [
        {"id": "T-2", "depends_on": ["T-1"]},
        {"id": "T-3", "depends_on": []},
    ]
    sorted_queue = sort_tasks_by_dependencies(queue, completed_task_ids={"T-1"})
    assert sorted_queue[0]["id"] in {"T-2", "T-3"}
    assert {t["id"] for t in sorted_queue} == {"T-2", "T-3"}


def test_ready_tasks_sorted_by_priority():
    queue = [
        {"id": "T-low", "depends_on": [], "priority": 9},
        {"id": "T-high", "depends_on": [], "priority": 1},
    ]
    sorted_queue = sort_tasks_by_dependencies(queue, completed_task_ids=set())
    assert [t["id"] for t in sorted_queue] == ["T-high", "T-low"]


def test_cyclic_dependency_does_not_crash_or_hang():
    """ADR-002: no cycle detection by design -- 2 task yang saling
    depends_on cuma tidak pernah 'ready', didorong ke belakang, TIDAK
    infinite loop atau exception."""
    queue = [
        {"id": "T-a", "depends_on": ["T-b"]},
        {"id": "T-b", "depends_on": ["T-a"]},
    ]
    sorted_queue = sort_tasks_by_dependencies(queue, completed_task_ids=set())
    assert {t["id"] for t in sorted_queue} == {"T-a", "T-b"}


def test_missing_depends_on_field_treated_as_no_dependency():
    """Compatibility: dict lama tanpa key 'depends_on' sama sekali tidak crash."""
    queue = [{"id": "T-1"}]
    sorted_queue = sort_tasks_by_dependencies(queue, completed_task_ids=set())
    assert sorted_queue[0]["id"] == "T-1"


# ---------------------------------------------------------------------------
# ToolRunner.execute_task() -- stop_condition wiring
# ---------------------------------------------------------------------------

def _runner_with_vault(tmp_path, session_id: str = "test-stop-cond") -> tuple[ToolRunner, EvidenceVault]:
    """`base_dir=tmp_path` -- EvidenceVault persist ke disk keyed by
    session_id; tanpa isolasi ini, test yang pakai session_id sama akan
    saling kontaminasi antar-run (ditemukan lewat kegagalan nyata saat
    menulis test ini, bukan diasumsikan)."""
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    perm = PermissionManager(PermissionMode.FULL_LAB)
    from wth_ai.config import WTHConfig
    runner = ToolRunner(WTHConfig.load(), perm, scope)
    vault = EvidenceVault(session_id, base_dir=tmp_path)
    runner.evidence_vault = vault
    return runner, vault


def test_evidence_stored_per_command_now_that_vault_is_wired(tmp_path):
    """Gap yang ditutup sekaligus: sebelumnya ToolRunner TIDAK PERNAH
    menyimpan evidence ke vault sama sekali dari jalur ini."""
    runner, vault = _runner_with_vault(tmp_path)
    task = ExecutorTask(
        id="T-evidence", goal="check", target="example.com",
        permission_required=PermissionMode.FULL_LAB,
        command_plan=[{"command": "cat /etc/hostname", "purpose": "probe"}],
        evidence_schema={},
    )
    runner.execute_task(task, approved=True)
    items = [e for e in vault._items if e["finding_id"] == "T-evidence"]
    assert len(items) == 1


def test_stop_condition_early_exit_when_schema_satisfied(tmp_path):
    """REGRESI-GUARD utama: stop_condition + evidence_schema yang match ->
    sisa command_plan TIDAK dijalankan (dead field sebelumnya sekarang
    punya efek nyata, sesuai acceptance criteria PRD-009 literal).

    Catatan jujur: validate_schema() (existing, tidak diubah WU ini) mengecek
    substring `schema["type"]` terhadap json.dumps() SELURUH entry -- karena
    setiap store() di sini mengoper evidence_schema=task.evidence_schema,
    field itu SENDIRI ikut ter-echo ke entry tersimpan, jadi "type" cocok
    begitu satu command tersimpan, TANPA butuh konten command benar-benar
    membuktikan apa pun. Ini kelemahan pre-existing validate_schema() (dipakai
    apa adanya di tempat lain juga, mis. engagement_loop.py), bukan sesuatu
    yang diperkenalkan WU ini -- didokumentasikan di sini secara eksplisit,
    bukan disembunyikan lewat test yang di-monkeypatch supaya kelihatan lebih
    ketat dari implementasi aslinya."""
    runner, vault = _runner_with_vault(tmp_path)
    task = ExecutorTask(
        id="T-stop", goal="check", target="example.com",
        permission_required=PermissionMode.FULL_LAB,
        command_plan=[
            {"command": "cat /etc/hostname", "purpose": "step 1"},
            {"command": "cat /etc/hostname", "purpose": "step 2 -- SHOULD NOT RUN"},
        ],
        evidence_schema={"type": "idor"},
        stop_condition="stop once idor evidence captured",
    )
    result = runner.execute_task(task, approved=True)
    assert len(result.commands_used) == 1, (
        f"REGRESI: stop_condition tidak menghentikan sisa command_plan -- commands_used={result.commands_used}"
    )
    assert any("stop_condition met early" in o for o in result.observations)


def test_no_stop_condition_runs_full_command_plan_unchanged(tmp_path):
    """Compatibility: task TANPA stop_condition tetap menjalankan seluruh
    command_plan seperti sebelumnya -- fix ini tidak mengubah default behavior."""
    runner, vault = _runner_with_vault(tmp_path)
    task = ExecutorTask(
        id="T-no-stop", goal="check", target="example.com",
        permission_required=PermissionMode.FULL_LAB,
        command_plan=[
            {"command": "cat /etc/hostname", "purpose": "step 1"},
            {"command": "cat /etc/hostname", "purpose": "step 2"},
        ],
        evidence_schema={"type": "idor"},
        stop_condition="",  # kosong -- tidak boleh early-exit
    )
    result = runner.execute_task(task, approved=True)
    assert len(result.commands_used) == 2


def test_no_evidence_vault_wired_runs_full_plan_without_crashing():
    """Compatibility/fail-safe: ToolRunner tanpa evidence_vault (None,
    default) TIDAK crash -- cuma tidak dapat early-exit."""
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["example.com"], authorized=True)
    perm = PermissionManager(PermissionMode.FULL_LAB)
    from wth_ai.config import WTHConfig
    runner = ToolRunner(WTHConfig.load(), perm, scope)
    assert runner.evidence_vault is None
    task = ExecutorTask(
        id="T-no-vault", goal="check", target="example.com",
        permission_required=PermissionMode.FULL_LAB,
        command_plan=[{"command": "cat /etc/hostname", "purpose": "step 1"}],
        stop_condition="whatever",
        evidence_schema={"type": "idor"},
    )
    result = runner.execute_task(task, approved=True)
    assert len(result.commands_used) == 1
    assert result.status == "success"


# ---------------------------------------------------------------------------
# Serialization -- kedua fungsi _task_to_dict/_task_dict include field baru
# ---------------------------------------------------------------------------

def test_orchestrator_task_to_dict_includes_new_fields():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    task = ExecutorTask(
        id="T-x", goal="g", target="x.com", permission_required=PermissionMode.STANDARD_TESTING,
        priority=2, depends_on=["T-y"], origin="chain_planner",
    )
    d = orch._task_to_dict(task)
    assert d["priority"] == 2
    assert d["depends_on"] == ["T-y"]
    assert d["origin"] == "chain_planner"


def test_supervisor_dispatch_task_dict_includes_new_fields():
    from wth_ai.agents.supervisor_dispatch import _task_dict

    task = ExecutorTask(
        id="T-y", goal="g", target="x.com", permission_required=PermissionMode.STANDARD_TESTING,
        priority=3, depends_on=["T-z"], origin="manual",
    )
    d = _task_dict(task)
    assert d["priority"] == 3
    assert d["depends_on"] == ["T-z"]
    assert d["origin"] == "manual"
