"""Supervisor tool dispatch — all agent-callable operations."""

from __future__ import annotations

import json
import time
from typing import Any

from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.engagement.mode import auto_approve_enabled
from wth_ai.models import AssessmentPhase, ExecutorTask, FindingState, PermissionMode, sort_tasks_by_dependencies


def dispatch_supervisor_tool(
    ctx: SupervisorContext,
    name: str,
    args: dict[str, Any],
    *,
    approved: bool = False,
) -> str:
    import os
    started = time.time()
    ctx.notify_tool_start(name, args)

    if ctx.action_memory is not None:
        blocked, reason = ctx.action_memory.should_block(name, args)
        if blocked:
            msg = f"SKIP (memory): {reason}"
            duration_ms = int((time.time() - started) * 1000)
            ctx.notify_tool_end(name, msg[:500], duration_ms)
            if ctx.persist_fn:
                ctx.persist_fn()
            ctx.action_memory.record(name, args, msg, duration_ms=duration_ms)
            return msg

    try:
        result = _dispatch(ctx, name, args, approved=approved)
    except Exception as exc:  # noqa: BLE001
        result = f"ERROR: {exc}"

    duration_ms = int((time.time() - started) * 1000)
    ctx.notify_tool_end(name, result[:500], duration_ms)
    if ctx.persist_fn:
        ctx.persist_fn()
    if ctx.action_memory is not None:
        ctx.action_memory.record(name, args, result, duration_ms=duration_ms)
    return result


def _dispatch(ctx: SupervisorContext, name: str, args: dict[str, Any], *, approved: bool) -> str:
    if name.startswith("mcp_"):
        return _dispatch_mcp(ctx, name, args)

    if name == "scope_set":
        ctx.notify_phase("scope", 5)
        target = args.get("target", "")
        in_scope = args.get("in_scope") or ([target] if target else [])
        ctx.scope_mgr.set_scope(
            target=target,
            # WU-011 (AGENT-01): default False, bukan True. "authorized" TIDAK
            # ada di required[] tool schema (tool_registry.py) -- LLM bisa
            # memanggil scope_set(target=...) tanpa pernah menyebut otorisasi
            # sama sekali dan sebelumnya lolos jadi authorized=True begitu
            # saja. Fail-closed law: MISSING -> DENY.
            authorized=args.get("authorized", False),
            environment=args.get("environment", "production"),
            in_scope=in_scope,
            rate_limit_rps=args.get("rate_limit_rps", ctx.scope_mgr.scope.rate_limit_rps),
        )
        ctx.state.scope = ctx.scope_mgr.scope
        if target and ctx.recon_bridge:
            ctx.recon_bridge.set_target(target)
        return f"Scope set: target={target} authorized={ctx.state.scope.authorized}"

    if name == "scope_show":
        return ctx.scope_mgr.display()

    if name == "permission_set":
        mode = args.get("mode", "read_only_recon")
        try:
            # `set_mode()` applies PERMISSION_ALIASES (so the legacy spellings
            # still resolve) and reports the VALID list on failure.
            # `PermissionMode(mode)` did neither, which is why three of the four
            # modes the tool schema advertised came back as "Invalid mode".
            pm = ctx.permission_mgr.set_mode(mode)
        except ValueError as exc:
            return f"Invalid mode: {mode} — {exc}"
        ctx.state.permission_mode = pm
        return f"Permission mode: {pm.value}"

    if name == "session_status":
        cov = ctx.asset_graph.coverage()
        verified = sum(
            1 for f in ctx.findings_db.findings.values() if f.state == FindingState.VERIFIED
        )
        return json.dumps({
            "session_id": ctx.state.session_id,
            "phase": ctx.state.current_phase.value,
            "agent_phase": ctx.current_phase,
            "target": ctx.state.scope.target,
            "permission": ctx.state.permission_mode.value,
            "graph_nodes": cov.get("nodes", 0),
            "coverage_pct": cov.get("coverage_pct", 0),
            "findings": len(ctx.findings_db.findings),
            "verified": verified,
            "queued_tasks": len(ctx.state.task_queue),
            "tools_run": ctx.tool_count,
        }, indent=2)

    if name == "recon_list_tools":
        ctx.notify_phase("recon", 15)
        if not ctx.recon_bridge:
            return "Recon bridge unavailable"
        tools = ctx.recon_bridge.list_tools(
            category=args.get("category"),
            available_only=bool(args.get("available_only")),
        )
        return json.dumps(tools[:30], indent=2)

    if name == "recon_run_tool":
        ctx.notify_phase("recon", 30)
        if not ctx.recon_bridge:
            return "Recon bridge unavailable"
        target = args.get("target") or ctx.state.scope.target
        if not target:
            return "ERROR: set scope target first"
        # Both the tool name and the target come from the model, and
        # `ReconAgentBridge` holds no ScopeManager of its own (`SafeToolRunner`
        # only checks binary availability), so nothing downstream would reject
        # an out-of-scope host. Verified on the pre-fix code: a `target`
        # argument was forwarded verbatim into `run_tool`.
        in_scope, scope_msg = ctx.scope_mgr.is_in_scope(target)
        if not in_scope:
            return f"ERROR: target out of scope — {scope_msg}"
        out = ctx.recon_bridge.run_tool(args.get("tool_name", ""), target=target)
        ctx.state.metadata["last_recon"] = ctx.recon_bridge.state_snapshot()
        ctx.state.metadata["graph_coverage"] = ctx.asset_graph.coverage()
        return json.dumps(out, indent=2)

    if name == "recon_finalize":
        ctx.notify_phase("recon", 50)
        if not ctx.recon_bridge:
            return "Recon bridge unavailable"
        out = ctx.recon_bridge.finalize()
        ctx.state.metadata["last_recon"] = out
        ctx.state.metadata["graph_coverage"] = ctx.asset_graph.coverage()
        ctx.state.current_phase = AssessmentPhase.RECON
        return json.dumps(out, indent=2)

    if name == "query_graph":
        if ctx.asset_graph.nodes:
            return ctx.asset_graph.export_for_planner(limit=int(args.get("limit", 40)))
        return "Graph empty — run recon tools first"

    if name == "store_evidence":
        tid = args.get("transcript_id", "")
        fid = args.get("finding_id", tid)
        if ctx.evidence_vault and tid:
            tx = ctx.terminal.transcripts.get(tid)
            if tx:
                ctx.evidence_vault.store(
                    finding_id=fid,
                    request={"method": tx.get("method", "GET"), "url": tx.get("url") or tx.get("command", "")},
                    response={"status": tx.get("status_code", tx.get("exit_code")), "body": tx.get("stdout", "")[:2000]},
                    evidence_schema={"type": args.get("vuln_class", "unknown")},
                )
                return f"Stored evidence [EV linked to TX:{tid}]"
        return "No vault or transcript"

    if name in ("http_probe", "run_shell"):
        auto = auto_approve_enabled(ctx.config)
        result = ctx.terminal.dispatch_tool(name, args, actor="supervisor", approved=approved or auto)
        if result.blocked and "approval" in (result.block_reason or "").lower():
            if ctx.callbacks.on_approval_required:
                ctx.callbacks.on_approval_required(result.transcript_id)
            return f"APPROVAL_REQUIRED:{result.transcript_id} — ask user to approve"
        return result.summary() + "\n" + (result.stdout[:3000] if result.stdout else result.stderr[:500])

    if name == "plan_hunt":
        ctx.notify_phase("plan", 60)
        if not ctx.planner:
            return "Planner unavailable"
        goal = args.get("goal", "")
        plan_result = ctx.planner.plan(
            goal, ctx.state,
            graph_context=ctx.asset_graph.export_for_planner(),
            skill_context=ctx.skill_router.fuse(goal).to_context() if ctx.skill_router else "",
        )
        ctx.state.planner_summary = plan_result.get("planner_summary", "")
        tasks = plan_result.get("tasks", [])
        ctx.state.task_queue = [_task_dict(t) for t in tasks]
        for hyp in plan_result.get("hypotheses", []):
            ctx.findings_db.add_hypothesis(hyp)
        ctx.state.current_phase = AssessmentPhase.HYPOTHESIS
        return json.dumps({
            "summary": plan_result.get("planner_summary", "")[:2000],
            "tasks": len(tasks),
            "hypotheses": len(plan_result.get("hypotheses", [])),
            "task_ids": [t.id for t in tasks[:10]],
        }, indent=2)

    if name == "execute_task":
        ctx.notify_phase("execute", 75)
        if not ctx.executor:
            return "Executor unavailable"
        task_id = args.get("task_id")
        task_dict = None
        if task_id:
            for td in ctx.state.task_queue:
                if td.get("id") == task_id:
                    task_dict = td
                    break
        elif ctx.state.task_queue:
            # WU-117 (PRD-009): advisory sort -- task dengan depends_on yang
            # belum masuk completed_tasks didorong ke belakang. Hanya
            # berlaku di jalur auto-select ini (task_id eksplisit di atas
            # TIDAK terpengaruh -- itu pilihan eksplisit pemanggil).
            completed_ids = {t.get("id") for t in ctx.state.completed_tasks}
            task_dict = sort_tasks_by_dependencies(ctx.state.task_queue, completed_ids)[0]
        if not task_dict:
            return "No task in queue"
        task = ExecutorTask.from_dict(task_dict)
        auto = auto_approve_enabled(ctx.config)
        result = ctx.executor.execute_module(task, ctx.state, approved=approved or auto or task.id in ctx.approved_tasks)
        ctx.state.completed_tasks.append(result.to_dict())
        ctx.state.task_queue = [t for t in ctx.state.task_queue if t.get("id") != task.id]
        ctx.state.current_phase = AssessmentPhase.DYNAMIC
        return json.dumps(result.to_dict(), indent=2)[:4000]

    if name == "verify_finding":
        ctx.notify_phase("verify", 88)
        fid = args.get("finding_id", "")
        finding = ctx.findings_db.findings.get(fid)
        if not finding:
            return f"Finding not found: {fid}"
        import os
        if ctx.client.available():
            v = ctx.verifier.verify(finding, ctx.state, evidence_vault=ctx.evidence_vault)
        elif os.environ.get("WTH_TEST_MODE") == "1":
            v = ctx.verifier.verify_offline(finding, ctx.evidence_vault)
        else:
            return "LLM unavailable for verification"
        verdict = v.get("verdict", "needs_more_evidence")
        if verdict == "verified":
            ctx.findings_db.update_finding_state(finding.id, FindingState.VERIFIED)
        elif verdict == "false_positive":
            ctx.findings_db.update_finding_state(finding.id, FindingState.FALSE_POSITIVE)
        return json.dumps(v, indent=2)

    if name == "list_findings":
        return ctx.findings_db.format_summary_table() or "No findings yet"

    if name == "generate_report":
        ctx.notify_phase("report", 100)
        if not ctx.reporter:
            return "Reporter unavailable"
        report = ctx.reporter.build(ctx.state, ctx.findings_db, evidence_vault=ctx.evidence_vault)
        ctx.state.current_phase = AssessmentPhase.REPORT
        return report[:12000]

    if name == "job_create_recon":
        if not ctx.job_manager:
            return "Job manager unavailable"
        target = args.get("target") or ctx.state.scope.target
        if not target:
            return "ERROR: no target"
        job = ctx.job_manager.create_job(
            "recon", target=target, profile=args.get("profile", "recon-only"),
            created_by="supervisor",
            metadata={"authorized": ctx.state.scope.authorized},
            permission_mode=ctx.state.permission_mode,
        )
        return json.dumps({"job_id": job.job_id, "status": job.status}, indent=2)

    if name == "job_poll":
        if not ctx.job_manager:
            return "Job manager unavailable"
        job = ctx.job_manager.get_job(args.get("job_id", ""))
        if not job:
            return "Job not found"
        return json.dumps(job.to_dict(), indent=2)

    if name == "delegate_task":
        if not ctx.orchestrator:
            return "ERROR: orchestrator unavailable"
        from wth_ai.agents.subagent.delegate import delegate_task
        depth = int(args.get("depth", 0))
        return delegate_task(
            ctx.orchestrator,
            str(args.get("subagent", "")),
            str(args.get("prompt", "")),
            depth=depth,
        )

    if name == "session_search":
        from wth_ai.memory.fts import SessionFtsIndex
        q = str(args.get("query", ""))
        limit = int(args.get("limit", 10))
        hits = SessionFtsIndex().search(q, limit=limit)
        return json.dumps(hits, indent=2)

    return f"Unknown tool: {name}"


def _task_dict(task: ExecutorTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "goal": task.goal,
        "target": task.target,
        "module": task.module,
        "command_plan": task.command_plan,
        "allowed_tools": task.allowed_tools,
        "permission_required": task.permission_required.value if task.permission_required else None,
        "evidence_schema": task.evidence_schema,
        "approval_needed": task.approval_needed,
        "priority": task.priority,
        "depends_on": task.depends_on,
        "origin": task.origin,
    }


def _dispatch_mcp(ctx: SupervisorContext, name: str, args: dict[str, Any]) -> str:
    try:
        from wth_ai.mcp.manager import MCPClientManager
        mgr = MCPClientManager(ctx.config)
        mgr.connect_all()
        parts = name.split("_", 2)
        if len(parts) < 3:
            return "Invalid MCP tool name"
        server, tool = parts[1], parts[2]
        return str(mgr.call_tool(server, tool, args))[:4000]
    except Exception as exc:  # noqa: BLE001
        return f"MCP error: {exc}"
