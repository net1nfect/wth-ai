"""Bridge job handlers to orchestrator components without full REPL."""

from __future__ import annotations

from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.models import ExecutorTask, PermissionMode, SessionState
from wth_ai.session_store import SessionStore


def load_session_for_job(job: Any) -> SessionState:
    """Load session state from job metadata or latest saved session."""
    config = WTHConfig.load()
    store = SessionStore(config)
    meta = getattr(job, "metadata", {}) or {}
    session_id = meta.get("session_id")
    if session_id:
        loaded = store.load(session_id)
        if loaded:
            return loaded
    loaded = store.load_latest()
    if loaded:
        return loaded
    return SessionState()


def build_executor_stack(state: SessionState) -> dict[str, Any]:
    """Construct executor/report dependencies for a job handler."""
    from wth_ai.agents.executor import ExecutorAgent
    from wth_ai.agents.report_builder import ReportBuilderAgent
    from wth_ai.evidence.vault import EvidenceVault
    from wth_ai.findings_db import FindingsDB
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.llm.router import LLMProviderRouter
    from wth_ai.permission_manager import PermissionManager
    from wth_ai.scope_manager import ScopeManager
    from wth_ai.terminal.bridge import AgentTerminalBridge
    from wth_ai.tool_runner import ToolRunner
    from wth_ai.tools.registry import ToolRegistry
    from wth_ai.tools.runner import ManifestToolRunner

    config = WTHConfig.load()
    client = LLMProviderRouter(config)
    scope_mgr = ScopeManager()
    scope_mgr.scope = state.scope
    perm_mgr = PermissionManager()
    perm_mgr.mode = state.permission_mode
    tool_runner = ToolRunner(config, perm_mgr, scope_mgr)
    tool_registry = ToolRegistry()
    manifest_runner = ManifestToolRunner(config, perm_mgr, scope_mgr, tool_registry)
    terminal = AgentTerminalBridge(config, perm_mgr, scope_mgr, state.session_id)
    graph = AssetGraph(state.session_id)
    vault = EvidenceVault(state.session_id)
    executor = ExecutorAgent(
        config, client, tool_runner, manifest_runner,
        terminal=terminal, evidence_vault=vault, asset_graph=graph,
    )
    reporter = ReportBuilderAgent(config, client)
    findings_db = FindingsDB()
    findings_db.load_from_dict({
        "findings": state.findings,
        "hypotheses": state.hypotheses,
    })
    return {
        "config": config,
        "state": state,
        "executor": executor,
        "reporter": reporter,
        "findings_db": findings_db,
        "evidence_vault": vault,
        "permission_mgr": perm_mgr,
    }


def run_executor_job(job: Any, ctx: Any) -> dict[str, Any]:
    """Execute queued tasks from session state (authorized, standard+)."""
    meta = getattr(job, "metadata", {}) or {}
    if not bool(meta.get("authorized", True)):
        return {"type": "executor", "status": "failed", "error": "not authorized"}

    state = load_session_for_job(job)
    if job.target:
        state.scope.target = job.target

    stack = build_executor_stack(state)
    perm = stack["permission_mgr"]
    if perm.mode == PermissionMode.READ_ONLY_RECON:
        return {
            "type": "executor",
            "status": "blocked",
            "error": "executor jobs require standard permission or higher",
        }

    tasks_raw = meta.get("tasks") or state.task_queue
    if not tasks_raw:
        return {"type": "executor", "status": "failed", "error": "no tasks queued"}

    approved = bool(meta.get("approved", False))
    results: list[dict[str, Any]] = []
    total = min(len(tasks_raw), 6)
    for i, td in enumerate(tasks_raw[:6]):
        if ctx.should_stop():
            return {"type": "executor", "status": "stopped", "results": results}
        ctx.progress(int((i / max(total, 1)) * 90), 100, f"task {i + 1}/{total}")
        task = ExecutorTask.from_dict(td)
        result = stack["executor"].execute(task, state, approved=approved)
        results.append(result.to_dict())
        state.completed_tasks.append(result.to_dict())
        state.task_queue = [t for t in state.task_queue if t.get("id") != task.id]

    SessionStore(stack["config"]).save(state)
    ctx.progress(100, 100, "done")
    return {
        "type": "executor",
        "status": "completed",
        "executed": True,
        "task_count": len(results),
        "results": results,
    }


def enrich_findings_for_report(state: SessionState, findings_db: Any) -> int:
    """Seed hypotheses from the asset graph when the session has none."""
    if findings_db.hypotheses or state.hypotheses:
        return 0
    from wth_ai.brain.hypothesis_engine import HypothesisEngine
    from wth_ai.graph.asset_graph import AssetGraph

    graph = AssetGraph(state.session_id)
    if not graph.nodes:
        return 0
    tech = (state.metadata.get("attack_surface_obj") or {}).get("tech_stack", {})
    added = 0
    for hyp in HypothesisEngine().generate(graph, tech)[:25]:
        findings_db.add_hypothesis(hyp)
        state.hypotheses.append(hyp)
        added += 1
    return added


def run_report_job(job: Any, ctx: Any) -> dict[str, Any]:
    """Build report from session findings."""
    state = load_session_for_job(job)
    stack = build_executor_stack(state)
    seeded = enrich_findings_for_report(state, stack["findings_db"])
    if seeded:
        ctx.log(f"seeded {seeded} hypotheses from asset graph")
    ctx.progress(20, 100, "building report")
    content = stack["reporter"].build(
        state, stack["findings_db"], evidence_vault=stack["evidence_vault"],
    )
    ctx.progress(80, 100, "saving report")
    path = stack["reporter"].save(content, state)
    from wth_ai.session_store import SessionStore

    SessionStore(stack["config"]).save(state)
    ctx.progress(100, 100, "done")
    return {
        "type": "report",
        "status": "completed",
        "path": str(path),
        "summary": content[:500],
        "hypotheses_seeded": seeded,
    }
