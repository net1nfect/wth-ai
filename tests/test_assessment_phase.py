"""Regression tests — WU-001: bug AssessmentPhase di supervisor_dispatch.py.

Bug asli: supervisor_dispatch.py mereferensikan AssessmentPhase.PLANNING/.EXECUTION/.REPORTING
yang tidak ada di enum (models.py hanya punya INTAKE/RECON/STATIC/DYNAMIC/HYPOTHESIS/VERIFY/
REPORT/STOPPED). AttributeError yang dihasilkan tertelan senyap oleh `except Exception` generik
di dispatch_supervisor_tool(), padahal side-effect (task_queue/completed_tasks/report) sudah
terjadi sebelum crash.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_NO_ANIMATION", "1")

from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
from wth_ai.config import WTHConfig
from wth_ai.models import AssessmentPhase, ExecutorResult, ExecutorTask, PermissionMode
from wth_ai.orchestrator import WTHOrchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID_PHASE_NAMES = {p.name for p in AssessmentPhase}


def test_no_invalid_assessment_phase_references():
    """Guardrail generik: setiap `AssessmentPhase.<NAME>` di source HARUS anggota enum valid.

    Mencegah kelas bug ini terulang di file manapun, bukan cuma titik yang sudah diperbaiki.
    """
    pattern = re.compile(r"AssessmentPhase\.([A-Z_]+)")
    offenders: list[str] = []
    for path in (REPO_ROOT / "wth_ai").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for name in pattern.findall(text):
            if name not in VALID_PHASE_NAMES:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: AssessmentPhase.{name}")
    assert not offenders, f"Referensi AssessmentPhase tidak valid ditemukan: {offenders}"


def _fresh_ctx():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    return orch, orch._supervisor_ctx


def test_plan_hunt_dispatch_sets_valid_phase_and_no_error():
    orch, ctx = _fresh_ctx()
    dispatch_supervisor_tool(ctx, "scope_set", {"target": "example.com", "authorized": True}, approved=True)

    stub_task = ExecutorTask(
        id="TASK-1",
        goal="probe example.com",
        target="example.com",
        permission_required=PermissionMode.READ_ONLY_RECON,
    )
    ctx.planner.plan = lambda *a, **k: {
        "planner_summary": "stub plan",
        "tasks": [stub_task],
        "hypotheses": [{"id": "HYP-1", "title": "stub hypothesis"}],
    }

    out = dispatch_supervisor_tool(ctx, "plan_hunt", {"goal": "test goal"}, approved=True)

    assert not out.startswith("ERROR:"), f"plan_hunt masih crash: {out}"
    assert ctx.state.current_phase == AssessmentPhase.HYPOTHESIS
    assert len(ctx.state.task_queue) == 1
    assert "HYP-1" in ctx.findings_db.hypotheses


def test_execute_task_dispatch_sets_valid_phase_and_no_error():
    orch, ctx = _fresh_ctx()
    dispatch_supervisor_tool(ctx, "scope_set", {"target": "example.com", "authorized": True}, approved=True)

    task = ExecutorTask(
        id="TASK-2",
        goal="probe example.com",
        target="example.com",
        permission_required=PermissionMode.READ_ONLY_RECON,
    )
    ctx.state.task_queue = [
        {
            "id": task.id, "goal": task.goal, "target": task.target, "module": task.module,
            "command_plan": task.command_plan, "allowed_tools": task.allowed_tools,
            "permission_required": task.permission_required.value,
            "evidence_schema": task.evidence_schema, "approval_needed": task.approval_needed,
        }
    ]
    stub_result = ExecutorResult(
        task_id=task.id, status="success", permission_check={"allowed": True},
        commands_used=[], output_summary="stub run", evidence=[], observations=[],
        risk="low", recommended_next_step="none",
    )
    ctx.executor.execute_module = lambda *a, **k: stub_result

    out = dispatch_supervisor_tool(ctx, "execute_task", {"task_id": task.id}, approved=True)

    assert not out.startswith("ERROR:"), f"execute_task masih crash: {out}"
    assert ctx.state.current_phase == AssessmentPhase.DYNAMIC
    assert ctx.state.completed_tasks, "hasil eksekusi harus tercatat di completed_tasks"
    assert all(t.get("id") != task.id for t in ctx.state.task_queue), "task selesai harus keluar dari queue"


def test_generate_report_dispatch_sets_valid_phase_and_no_error():
    orch, ctx = _fresh_ctx()
    dispatch_supervisor_tool(ctx, "scope_set", {"target": "example.com", "authorized": True}, approved=True)

    # Stub reporter.build agar test deterministik terlepas dari apakah ada LLM/Ollama
    # reachable di environment (out of scope WU-001 — lihat catatan di laporan WU).
    ctx.reporter.build = lambda *a, **k: "# Stub Security Assessment Report\n\nNo findings."

    out = dispatch_supervisor_tool(ctx, "generate_report", {}, approved=True)

    assert not out.startswith("ERROR:"), f"generate_report masih crash: {out}"
    assert ctx.state.current_phase == AssessmentPhase.REPORT
    assert out.strip(), "isi report tidak boleh kosong/hilang"
