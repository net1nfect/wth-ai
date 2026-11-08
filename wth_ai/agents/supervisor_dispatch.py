"""Supervisor tool dispatch — all agent-callable operations."""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.engagement.mode import auto_approve_enabled
from wth_ai.models import (
    AssessmentPhase,
    ExecutorTask,
    FindingState,
    PermissionMode,
    sort_tasks_by_dependencies,
)
from wth_ai.permission_presets import apply_preset
from wth_ai.verify_gate import apply_verification


def _dispatch_computer_use(ctx: SupervisorContext, args: dict[str, Any], *, approved: bool) -> str:
    """Computer use, gated twice: capability tier AND human approval.

    Reading the screen (screenshot/windows/screen_size/cursor) is observation and
    runs from read-only upward. Injecting input (click/move/type/key/focus) acts
    on whatever window the operator currently has focused, so it needs at least
    the `ask` preset's capability AND, unless approvals are disabled
    (`auto`/`yolo`), an explicit approval -- exactly the tiering the presets
    describe.
    """
    from wth_ai.computer_use import INPUT_ACTIONS, ComputerUse

    action = str(args.get("action", "")).strip().lower()
    if not action:
        return "computer_use: argumen 'action' wajib."

    if action in INPUT_ACTIONS:
        if not ctx.permission_mgr.mode_sufficient(PermissionMode.STANDARD_TESTING):
            return (
                f"DITOLAK: '{action}' menyuntik input ke desktop operator dan butuh minimal preset "
                f"'ask' (permission standard). Sekarang: {ctx.permission_mgr.mode.value}. "
                f"Pakai /permission ask atau /permission yolo."
            )
        if not approved and not auto_approve_enabled(getattr(ctx, "config", None)):
            return (
                f"BUTUH APPROVAL: '{action}' akan menggerakkan mouse/keyboard operator. "
                f"Setujui aksinya, atau jalankan /permission auto (atau yolo) kalau memang mau "
                f"tanpa konfirmasi."
            )

    kwargs = {k: v for k, v in args.items() if k != "action"}
    result = ComputerUse().run_action(action, **kwargs)

    if ctx.action_memory is not None:
        # memory best-effort: kegagalan mencatat tidak boleh menggagalkan aksi
        with contextlib.suppress(Exception):
            ctx.action_memory.record(f"computer_use:{action}", kwargs, result.describe()[:400])

    if not result.ok:
        return f"GAGAL {result.describe()} — jalankan action=detect untuk melihat backend yang terdeteksi."
    return result.describe()


def _dispatch_research(ctx: SupervisorContext, args: dict[str, Any]) -> str:
    """Public-information research. No approval gate, deliberately.

    Searching the web and reading a public page is read-only and touches no
    target asset, so prompting for it would be friction with no safety value.
    What it DOES get is the epistemic tag: every note is a candidate, and the
    rendered output says so on every call.
    """
    from wth_ai.research import get_researcher

    action = str(args.get("action", "")).strip().lower()
    session_id = getattr(getattr(ctx, "state", None), "session_id", "") or ""
    researcher = get_researcher(session_id)

    if action == "search":
        query = str(args.get("query", "")).strip()
        if not query:
            return "research: argumen 'query' wajib untuk action=search."
        limit = int(args.get("limit", 8) or 8)
        return researcher.report(researcher.search(query, limit=limit))

    if action in ("fetch", "browser"):
        url = str(args.get("url", "")).strip()
        if not url:
            return f"research: argumen 'url' wajib untuk action={action}."
        note = researcher.fetch(url) if action == "fetch" else researcher.browser_fetch(url)
        if not note.ok and action == "browser":
            # A browser failure is not a dead end: say so and fall back.
            fallback = researcher.fetch(url)
            return (
                f"{researcher.report(note)}\n\n"
                f"— fallback fetch biasa: {researcher.report(fallback)}"
            )
        return researcher.report(note)

    if action == "notes":
        block = researcher.context_block()
        return block or "Belum ada catatan riset di sesi ini."

    return f"research: aksi tidak dikenal {action!r}. Pilihan: search, fetch, browser, notes."


def dispatch_supervisor_tool(
    ctx: SupervisorContext,
    name: str,
    args: dict[str, Any],
    *,
    approved: bool = False,
) -> str:
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


def _dispatch_agent_graph(ctx: SupervisorContext, name: str, args: dict[str, Any]) -> str:
    """Tool `agent_*` di atas satu `AgentGraph` milik ctx.

    Graf dibuat lazy di sini (bukan di constructor) supaya sesi supervisor yang
    tidak pernah memakai fan-out tidak membayar apa pun — termasuk thread dan
    penulisan state ke `$WTH_HOME/runtime/agents/`.
    """
    from wth_ai.agents.graph import AgentGraph

    graph = getattr(ctx, "agent_graph", None)
    if graph is None:
        graph = AgentGraph(orchestrator=ctx.orchestrator)
        ctx.agent_graph = graph

    if name == "agent_spawn":
        parent_id = args.get("parent_id")
        result = graph.spawn(
            str(args.get("subagent", "")),
            str(args.get("prompt", "")),
            parent_id=str(parent_id) if parent_id else None,
        )
    elif name == "agent_send":
        result = graph.send(str(args.get("node_id", "")), str(args.get("message", "")))
    elif name == "agent_wait":
        # LLM boleh mengirim `timeout` sebagai string; nilai non-angka harus jadi
        # error yang bisa dibaca model, bukan ValueError yang memutus tool loop.
        try:
            timeout = float(args.get("timeout") or 30.0)
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": "timeout harus angka detik"})
        result = graph.wait(args.get("node_id") or None, timeout=timeout)
    else:
        result = {"ok": True, **graph.tree()}
    return json.dumps(result, indent=2)


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

    if name == "research":
        return _dispatch_research(ctx, args)

    if name == "computer_use":
        return _dispatch_computer_use(ctx, args, approved=approved)

    if name == "permission_set":
        mode = str(args.get("mode", "ask"))
        try:
            # `apply_preset` sets the three safety dimensions TOGETHER (tool
            # ceiling + approval), so the mode can never end up half-applied.
            # Raw PermissionMode values still resolve through ALIASES.
            preset = apply_preset(mode, permission_mgr=ctx.permission_mgr)
        except ValueError as exc:
            return f"Invalid mode: {mode} — {exc}"
        ctx.state.permission_mode = ctx.permission_mgr.mode
        return f"Permission preset: {preset.name} — {preset.summary}"

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

    if name == "propose_payload":
        from wth_ai.hunt.generator import save_generated, validate_generated

        payload, error = validate_generated(args)
        if payload is None:
            return f"PAYLOAD DITOLAK: {error}"
        saved = save_generated([payload])
        notes = [f"Payload `{payload.id}` diterima (blast={payload.blast_radius})."]
        if saved:
            notes.append(f"Tersimpan di {saved}.")
        # Batas yang tidak bisa dilewati model: massal tidak pernah dijalankan.
        if payload.blast_radius == "mass" or not payload.auto_runnable:
            notes.append(
                "TIDAK dijalankan: berdampak massal (dokumentasi saja). "
                f"Padanan berbatas: {payload.bounded_alternative or 'belum dinyatakan'}"
            )
            return "\n".join(notes)
        destructive_allowed = bool(getattr(getattr(ctx.state, "scope", None), "destructive_allowed", False))
        if payload.requires_destructive and not destructive_allowed:
            notes.append("TIDAK dijalankan: destruktif tapi engagement belum membuka opt-in "
                         "(`/scope set destructive=yes`).")
            return "\n".join(notes)
        if not args.get("run_now", True):
            notes.append("Tidak dijalankan atas permintaan (run_now=false).")
            return "\n".join(notes)
        auto = auto_approve_enabled(ctx.config)
        method = str(payload.probe.get("method", "GET")).upper()
        actor = "recon" if method in {"GET", "HEAD", "OPTIONS"} else "executor"
        result = ctx.terminal.dispatch_tool("http_probe", dict(payload.probe),
                                            actor=actor, approved=approved or auto)
        if result.blocked:
            notes.append(f"Diblokir: {result.block_reason}")
            return "\n".join(notes)
        notes.append(f"Dijalankan: {result.summary()}")
        if result.analysis:
            notes.append(result.analysis)
        return "\n".join(notes)

    if name in ("http_probe", "run_shell"):
        auto = auto_approve_enabled(ctx.config)
        result = ctx.terminal.dispatch_tool(name, args, actor="supervisor", approved=approved or auto)
        if result.blocked and "approval" in (result.block_reason or "").lower():
            if ctx.callbacks.on_approval_required:
                ctx.callbacks.on_approval_required(result.transcript_id)
            return f"APPROVAL_REQUIRED:{result.transcript_id} — ask user to approve"
        if name == "http_probe":
            # Pengujian adaptif: agent perlu (a) SINYAL respons, (b) pembanding
            # terhadap request sebelumnya, dan (c) pasangan request/response yang
            # masuk ke rantai bukti supaya /verify dan scoring bisa memakainya.
            from wth_ai.terminal.analysis import diff_responses, evidence_pair

            parts = [result.summary()]
            if result.analysis:
                parts.append(result.analysis)
            compare_to = str(args.get("compare_to") or "").strip()
            if compare_to:
                prior = ctx.terminal.transcripts.get(compare_to)
                if prior:
                    parts.append(diff_responses(prior, result.to_dict()))
                else:
                    parts.append(f"[DIFF vs TX] baseline {compare_to} tidak ditemukan")
            if not result.blocked:
                ctx.state.metadata.setdefault("request_pairs", []).append(
                    evidence_pair(result, role=str(args.get("purpose") or "adaptive"))
                )
            if result.stdout:
                parts.append(result.stdout[:3000])
            elif result.stderr:
                parts.append(result.stderr[:500])
            return "\n".join(part for part in parts if part)
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
            completed_ids = {str(t.get("id")) for t in ctx.state.completed_tasks
                             if t.get("id") is not None}
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
        # P0-5: satu fungsi bersama dengan `orchestrator._verify` dan
        # `engagement_loop`; state hanya bergerak lewat gate veracity.
        apply_verification(ctx.findings_db, finding, v, ctx.evidence_vault,
                           actor_role="discoverer")
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

    if name in ("agent_spawn", "agent_send", "agent_wait", "agent_tree"):
        return _dispatch_agent_graph(ctx, name, args)

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
