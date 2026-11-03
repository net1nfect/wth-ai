"""Regression tests — WU-006: Loop state machine enforcement-by-construction
(`LoopStateMachine`, rename from `EngagementStateMachine`, ADR-011, PRD-008).

Bug asli: `TRANSITIONS` + `.transition()` benar secara desain tapi TIDAK
PERNAH ditegakkan struktural -- `state` adalah plain public attribute,
`engagement_loop.py` menulis `self.sm.state = LoopState.X` LANGSUNG di 6
titik, bypass total guard (write-only/vestigial -- `.transition()` sendiri
tidak pernah dipanggil di manapun sebelum WU ini).

Scope (lebih sempit dari draft PRD-008 -- lihat "Scope decision" di
WORK-UNITS.md): hanya `EngagementLoop`/`LoopStateMachine` (bug konkret,
reachable & live via `orchestrator.py::_run_hunt` -> `/hunt`). Wiring
`AgentToolLoop`/`dispatch_supervisor_tool()` ke state machine yang SAMA
adalah net-new integration (bukan bug fix -- AgentToolLoop tidak punya state
machine sama sekali hari ini, tidak ada yang "dilewati"), DITUNDA sebagai WU
terpisah. `LoopBudget` (graceful degradation) adalah P1 di PRD-008 sendiri,
bukan bagian WU-006.

Temuan penting selama trace-before-enforce: TRANSITIONS asli (ditulis saat
`.transition()` masih dead code, tidak pernah divalidasi terhadap flow
nyata) TIDAK cocok dengan 2 jalur legitimate di `engagement_loop.py`:
- planner menghasilkan 0 task -> break dini -> lapor (PLANNING->REPORTING,
  sebelumnya tidak ada di TRANSITIONS).
- loop kehabisan max_iterations tanpa finding critical/high -> lapor
  (REPLANNING->REPORTING, sebelumnya tidak ada di TRANSITIONS).
Kedua edge ini DITAMBAHKAN ke TRANSITIONS (koreksi tabel, bukan pelemahan
enforcement) -- dibuktikan lewat test end-to-end di bawah yang benar-benar
menjalankan `EngagementLoop.run()` lewat kedua jalur ini tanpa exception.

Juga ditemukan: batch multi-task (`task_queue[:3]`) sebelumnya men-set
VERIFYING per-task di dalam inner loop -- task ke-2 akan diam-diam "execute"
sementara state masih VERIFYING (bukan EXECUTING). Diperbaiki dengan
memindah transisi VERIFYING jadi SEKALI setelah seluruh batch selesai.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.loop.state_machine import InvalidTransitionError, LoopState, LoopStateMachine


# ---------------------------------------------------------------------------
# Enforcement-by-construction: state read-only, transition() sole mutator
# ---------------------------------------------------------------------------

def test_state_attribute_assignment_raises_attributeerror():
    """PRD-008 acceptance criterion literal: `self.sm.state = X` di mana pun
    menghasilkan AttributeError -- bukti enforcement struktural, bukan konvensi."""
    sm = LoopStateMachine()
    with pytest.raises(AttributeError):
        sm.state = LoopState.RECON  # type: ignore[misc]


def test_transition_to_valid_state_succeeds():
    sm = LoopStateMachine()
    assert sm.state == LoopState.IDLE
    assert sm.transition(LoopState.RECON) is True
    assert sm.state == LoopState.RECON


def test_transition_to_stopped_always_allowed_from_any_state():
    for start in (LoopState.RECON, LoopState.PLANNING, LoopState.EXECUTING, LoopState.VERIFYING):
        sm = LoopStateMachine()
        sm._state = start  # seed langsung utk unit test, bukan lewat transition()
        assert sm.transition(LoopState.STOPPED) is True
        assert sm.state == LoopState.STOPPED


# ---------------------------------------------------------------------------
# Transisi ilegal -- raise di WTH_TEST_MODE, clamp di luar itu
# ---------------------------------------------------------------------------

def test_illegal_transition_raises_in_test_mode(monkeypatch):
    monkeypatch.setenv("WTH_TEST_MODE", "1")
    sm = LoopStateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition(LoopState.EXECUTING)  # IDLE -> EXECUTING tidak valid


def test_illegal_transition_clamps_outside_test_mode(monkeypatch):
    """Di luar WTH_TEST_MODE/WTH_STRICT_SM: clamp -- state TIDAK berubah,
    return False, tidak raise, tidak pura-pura sukses (PRD-008 FR)."""
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WTH_STRICT_SM", raising=False)
    sm = LoopStateMachine()
    result = sm.transition(LoopState.EXECUTING)
    assert result is False
    assert sm.state == LoopState.IDLE  # tidak korup


def test_illegal_transition_raises_with_strict_sm_env_even_without_test_mode(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("WTH_STRICT_SM", "1")
    sm = LoopStateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition(LoopState.DONE)


def test_invalid_transition_emits_event_on_bus():
    events = []

    class FakeBus:
        def emit(self, event_type, **kwargs):
            events.append((event_type, kwargs))

    sm = LoopStateMachine(bus=FakeBus())
    with pytest.raises(InvalidTransitionError):
        sm.transition(LoopState.EXECUTING)
    assert events
    assert events[0][0] == "invalid_state_transition"
    assert events[0][1]["data"]["from"] == "idle"
    assert events[0][1]["data"]["to"] == "executing"


# ---------------------------------------------------------------------------
# TRANSITIONS -- koreksi tabel (edge yang ditambahkan, DAN yang tetap ilegal)
# ---------------------------------------------------------------------------

def test_planning_to_reporting_now_legal():
    """Koreksi tabel: planner 0 task -> break dini -> report."""
    sm = LoopStateMachine()
    sm._state = LoopState.PLANNING
    assert sm.transition(LoopState.REPORTING) is True


def test_replanning_to_reporting_now_legal():
    """Koreksi tabel: loop kehabisan iterasi -> report."""
    sm = LoopStateMachine()
    sm._state = LoopState.REPLANNING
    assert sm.transition(LoopState.REPORTING) is True


def test_genuinely_illegal_transitions_still_rejected(monkeypatch):
    """Koreksi tabel TIDAK melonggarkan segalanya -- transisi yang memang
    tidak masuk akal tetap ditolak."""
    monkeypatch.setenv("WTH_TEST_MODE", "1")
    cases = [
        (LoopState.IDLE, LoopState.EXECUTING),
        (LoopState.IDLE, LoopState.REPORTING),
        (LoopState.RECON, LoopState.EXECUTING),
        (LoopState.EXECUTING, LoopState.REPORTING),
        (LoopState.EXECUTING, LoopState.PLANNING),
    ]
    for start, target in cases:
        sm = LoopStateMachine()
        sm._state = start
        with pytest.raises(InvalidTransitionError):
            sm.transition(target)


# ---------------------------------------------------------------------------
# Rename -- EngagementStateMachine tidak lagi ada, LoopStateMachine exported
# ---------------------------------------------------------------------------

def test_engagement_state_machine_name_removed():
    """ADR-011: rename total, bukan alias kompatibilitas -- tidak ada import
    lama yang tersisa di seluruh codebase (sudah diverifikasi via grep saat
    implementasi), jadi tidak perlu shim."""
    import wth_ai.loop.state_machine as mod
    assert not hasattr(mod, "EngagementStateMachine")


def test_loop_state_machine_exported_from_package():
    from wth_ai.loop import LoopState, LoopStateMachine  # noqa: F401


# ---------------------------------------------------------------------------
# End-to-end -- EngagementLoop.run() melalui jalur yang butuh edge baru
# ---------------------------------------------------------------------------

def test_engagement_loop_reaches_reporting_via_no_tasks_early_break():
    """End-to-end: planner mengembalikan 0 task -> PLANNING->REPORTING harus
    tidak raise (membuktikan edge baru benar-benar dibutuhkan & bekerja,
    bukan cuma unit test terisolasi)."""
    from unittest.mock import MagicMock, patch

    from wth_ai.config import WTHConfig
    from wth_ai.loop.engagement_loop import EngagementLoop
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.update_from_dict({"target": "example.com", "in_scope": ["example.com"], "authorized": True})
    orch.state.scope = orch.scope_mgr.scope
    orch.state.metadata["last_recon"] = {"job_id": "mock-job", "target": "example.com"}

    plan_result = {"planner_summary": "no tasks", "hypotheses": [], "tasks": []}
    loop = EngagementLoop(orch)
    with patch.object(orch.planner, "plan", return_value=plan_result):
        with patch.object(orch, "_report", return_value="## Report\nmock report"):
            out = loop.run("hunt with no chains", max_iterations=1)

    assert "no tasks" in out
    # run() berhenti di REPORTING (tidak pernah transition ke DONE) -- kalau
    # PLANNING->REPORTING ilegal, baris ini tidak akan pernah tercapai sama
    # sekali (InvalidTransitionError akan raise duluan di dalam run()).
    assert loop.sm.state == LoopState.REPORTING


def test_engagement_loop_handles_multi_task_batch_without_raising():
    """End-to-end: 2 task dalam satu batch (task_queue[:3]) tidak boleh
    memicu InvalidTransitionError -- membuktikan fix pemindahan transisi
    VERIFYING (sekali per batch, bukan per-task) benar-benar menutup celah."""
    from unittest.mock import MagicMock, patch

    from wth_ai.config import WTHConfig
    from wth_ai.loop.engagement_loop import EngagementLoop
    from wth_ai.models import ExecutorTask
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.scope_mgr.update_from_dict({"target": "example.com", "in_scope": ["example.com"], "authorized": True})
    orch.state.scope = orch.scope_mgr.scope
    orch.state.metadata["last_recon"] = {"job_id": "mock-job", "target": "example.com"}

    tasks = [
        ExecutorTask(id="T-1", goal="Check IDOR", module="idor", target="https://api.example.com/a",
                     permission_required=orch.state.permission_mode, evidence_schema={"type": "idor"}),
        ExecutorTask(id="T-2", goal="Check IDOR 2", module="idor", target="https://api.example.com/b",
                     permission_required=orch.state.permission_mode, evidence_schema={"type": "idor"}),
    ]
    plan_result = {"planner_summary": "two tasks", "hypotheses": [], "tasks": tasks}
    exec_result = MagicMock()
    exec_result.status = "completed"
    exec_result.to_dict.return_value = {"id": "T-x", "status": "completed", "output": "ok"}

    loop = EngagementLoop(orch)
    with patch.object(orch.planner, "plan", return_value=plan_result):
        with patch.object(orch.executor, "execute_module", return_value=exec_result):
            with patch.object(orch.evidence_vault, "validate_schema", return_value=(True, [])):
                with patch.object(orch.verifier, "verify_offline", return_value={"verdict": "needs_more_evidence"}):
                    with patch.object(orch, "_report", return_value="## Report\nmock report"):
                        out = loop.run("hunt two tasks", max_iterations=1)

    assert "Task T-1" in out
    assert "Task T-2" in out
