"""Supervisor tool dispatch — all agent-callable operations."""

from __future__ import annotations

import json
import time
from typing import Any

from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.engagement.mode import auto_approve_enabled, is_no_limits
from wth_ai.models import AssessmentPhase, ExecutorTask, FindingState, PermissionMode, sort_tasks_by_dependencies
from wth_ai.permission_presets import apply_preset


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

    if action in INPUT_ACTIONS and not is_no_limits(getattr(ctx, "config", None)):
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
        try:
            ctx.action_memory.record(f"computer_use:{action}", kwargs, result.describe()[:400])
        except Exception:  # noqa: BLE001 - memory is best-effort, never fatal
            pass

    if not result.ok:
        return f"GAGAL {result.describe()} — jalankan action=detect untuk melihat backend yang terdeteksi."
    return result.describe()


def _dispatch_oob_create(ctx: SupervisorContext, args: dict[str, Any]) -> str:
    """Buat token callback OOB (F2).

    Sengaja TANPA approval gate dan TANPA parameter host/port:
    * membuat token + listener loopback tidak menyentuh target apa pun;
    * host/port adalah permukaan OPERATOR (slash `/oob start --host`), bukan
      permukaan LLM — kalau LLM bisa memindahkan bind ke 0.0.0.0, ia bisa
      membuka port ke jaringan; itu tidak boleh berasal dari argumen model.
    """
    from wth_ai.oob import get_oob_service

    svc = get_oob_service()
    if not svc.running:
        svc.start()
    label = str(args.get("label", "") or "callback")
    ttl = args.get("ttl_minutes")
    state = ctx.state
    entry = svc.create(
        label=label,
        ttl_minutes=int(ttl) if ttl else None,
        session_id=getattr(state, "session_id", ""),
        target=getattr(getattr(state, "scope", None), "target", ""),
    )
    return (
        f"OOB token dibuat: `{label}`\n"
        f"url: {entry['url']}\n"
        f"token: {entry['token']}\n"
        f"expires: {entry['expires_at']}\n"
        "Kirim URL ini ke vektor yang diuji (mis. parameter url=, XML entity, template), "
        "lalu panggil oob_check(token=...) untuk melihat hit."
    )


def _dispatch_oob_dns(ctx: SupervisorContext, args: dict[str, Any]) -> str:
    """Nyalakan listener DNS callback (F2) dan kembalikan hostname-nya.

    Sama seperti `oob_create`: tidak menyentuh target, tidak punya parameter
    host — membuka DNS ke jaringan tetap keputusan operator.
    """
    from wth_ai.oob import get_oob_service

    svc = get_oob_service()
    port = args.get("port")
    st = svc.start_dns(port=int(port) if port is not None else None)
    entry = svc.create(
        label=f"dns:{getattr(ctx.state, 'session_id', '')[:8] or 'cb'}",
        session_id=getattr(getattr(ctx, "state", None), "session_id", ""),
        target=getattr(getattr(getattr(ctx, "state", None), "scope", None), "target", ""),
    )
    return (
        f"DNS callback listener aktif di {st['host']}:{st['port']} (query={st['queries']}).\n"
        f"hostname callback: {entry['dns']}\n"
        f"token: {entry['token']}\n"
        "Kirim hostname itu ke vektor (mis. entity XML SYSTEM, URL berisi hostname, perintah "
        "yang memicu resolusi), lalu panggil oob_check(token=...) untuk melihat hit."
    )


def _dispatch_oob_check(ctx: SupervisorContext, args: dict[str, Any]) -> str:
    """Periksa hit callback; bila ada, simpan sebagai transcript [TX:...]."""
    from wth_ai.oob import format_hits, get_oob_service

    svc = get_oob_service()
    token = str(args.get("token", "")).strip()
    if not token:
        return "oob_check: argumen 'token' wajib (dari oob_create)."
    entry = svc.store.get_token(token) or {"token": token, "label": ""}
    hits = svc.store.hits(token)
    if not hits:
        return (
            f"Belum ada hit untuk `{entry.get('label') or token[:10]}…`. "
            "Jika target belum dihubungi, tunggu; jika sudah, periksa apakah URL callback persis sama."
        )
    tx = svc.record_as_transcript(ctx.terminal.transcripts, token=token, entry=entry, hits=hits)
    return f"{format_hits(entry, hits)}\n\nbukti tersimpan: [TX:{tx}] — kutip ini sebagai bukti callback (bukan klaim)."


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




def _tx_stub(terminal: Any, transcript_id: str) -> Any:
    stored = getattr(getattr(terminal, "transcripts", None), "get", None)
    if stored is not None:
        try:
            got = stored(transcript_id)
            if got is not None:
                return got
        except Exception:  # noqa: BLE001
            pass
    return None

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

    # Strix-style Python Runtime: the agent writes its own exploit/validation
    # script and executes it on the spot through the same run_shell path
    # (same permission/approval/audit) -- no sandbox simulation.
    if name == "python_runtime":
        code = str(args.get("code", "") or "")
        if not code.strip():
            return "ERROR: python_runtime butuh 'code'."
        name = "run_shell"
        args = {
            "command": f"python3 - <<'WTHPYEOF'\n{code}\nWTHPYEOF",
            "purpose": str(args.get("purpose") or "python exploit runtime"),
            # GAP-007 versi tanpa-Docker: kode agent-written eksekusi di
            # clean-room (throwaway cwd/HOME + env dipangkas), bukan di host
            # bersih. Map ke run_shell cleanroom=True — jalur permission/audit
            # tetap sama.
            "cleanroom": True,
        }

    if name == "self_improve":
        from wth_ai.agents.agent_memory import write_lesson

        lesson = str(args.get("lesson", "") or "").strip()
        if not lesson:
            return "ERROR: self_improve butuh 'lesson'."
        entry = write_lesson(
            str(args.get("topic", "general")),
            lesson,
            evidence=str(args.get("evidence", "") or ""),
        )
        return (
            f"LESSON TERSIMPAN ({entry['id']}): topic={entry['topic']} — {lesson[:200]}"
            " (pelah Barclay teratas akan di-inject ke context berikutnya)"
        )

    if name == "memory_write":
        from wth_ai.agents.agent_memory import write_note

        key = write_note(str(args.get("key", "note") or "note"),
                         str(args.get("value", "") or ""))
        return f"Memory tersimpan: {key} (persist antar sesi)."

    if name == "memory_read":
        from wth_ai.agents.agent_memory import read_note, read_lessons

        topic = str(args.get("lesson_topic", "") or "")
        notes = read_note(str(args.get("key", "") or ""))
        lessons = read_lessons(topic, limit=int(args.get("limit", 10) or 10))
        return json.dumps({"notes": notes, "lessons": lessons}, indent=2)[:6000]

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
            authorized=args.get("authorized", True if is_no_limits(ctx.config) else False),
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

    if name == "malware_forge":
        from wth_ai.malware import ForgeRequest, forge, format_result

        try:
            req = ForgeRequest(
                language=str(args.get("language", "c")),
                kind=str(args.get("kind", "loader")),
                target_os=str(args.get("os", "windows")),
                arch=str(args.get("arch", "x64")),
                c2=str(args.get("c2", "") or ""),
                features=set(args.get("features") or ["amsi", "etw"]),
                build=bool(args.get("build", True)),
                name=str(args.get("name", "") or ""),
                payload=str(args.get("payload", "") or ""),
                opts={str(k): str(v) for k, v in (args.get("opts") or {}).items()},
            )
        except ValueError as exc:
            return f"FORGE GAGAL: {exc}"
        out = forge(req, build=req.build)
        ctx.state.metadata["last_malware"] = str(out.output_dir)
        report = format_result(out)
        if out.artifacts:
            import json as _json
            meta = out.output_dir / "metadata.json"
            if meta.exists():
                try:
                    data = _json.loads(meta.read_text(encoding="utf-8"))
                    analyses = data.get("analysis") or []
                    if analyses:
                        from wth_ai.malware import format_report
                        report += "\n\n" + "\n\n".join(format_report(a) for a in analyses)
                except Exception:  # noqa: BLE001
                    pass
        return report

    if name == "malware_encode":
        from wth_ai.malware.encoder import encode

        source = str(args.get("payload", "") or "")
        if not source:
            return "malware_encode: argumen 'payload' (path atau hex) wajib."
        try:
            return encode(source, key=int(args.get("key", 0x5A)), fmt=str(args.get("format", "c")))
        except (ValueError, OSError) as exc:
            return f"ENCODE GAGAL: {exc}"

    if name == "malware_analyze":
        from wth_ai.malware import analyze, format_report

        target = str(args.get("path", "") or "")
        if not target:
            return "malware_analyze: argumen 'path' wajib."
        return format_report(analyze(target))

    if name == "oob_create":
        return _dispatch_oob_create(ctx, args)

    if name == "oob_dns":
        return _dispatch_oob_dns(ctx, args)

    if name == "oob_check":
        return _dispatch_oob_check(ctx, args)

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
            "no_limits": is_no_limits(ctx.config),
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
        # (Kecuali operator menyalakan godmode/WTH_NO_LIMITS.)
        if (payload.blast_radius == "mass" or not payload.auto_runnable) and not is_no_limits(ctx.config):
            notes.append(
                "TIDAK dijalankan: berdampak massal (dokumentasi saja). "
                f"Padanan berbatas: {payload.bounded_alternative or 'belum dinyatakan'}"
            )
            return "\n".join(notes)
        destructive_allowed = bool(getattr(getattr(ctx.state, "scope", None), "destructive_allowed", False))
        if payload.requires_destructive and not destructive_allowed and not is_no_limits(ctx.config):
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

    if name == "pipeline_surface":
        from wth_ai.pipeline.models import Surface

        surface = Surface.from_target(
            str(args.get("target", "") or ""),
            method=str(args.get("method", "GET") or "GET"),
            headers=args.get("headers") or {},
            body=str(args.get("body", "") or ""),
        )
        data = surface.to_dict()
        data["message"] = "Inventory saja — belum ada request ke target."
        return json.dumps(data, indent=2)

    if name == "pipeline_discover":
        from wth_ai.models import PermissionMode as _PermissionMode
        from wth_ai.pipeline.discovery import (
            discover_document,
            discover_from_js,
            external_script_urls,
            js_import_urls,
        )
        from wth_ai.pipeline.models import RequestSpec, Surface
        from wth_ai.pipeline.session import ProbeSession, scope_guard_from

        target = str(args.get("target") or ctx.state.scope.target or "").strip()
        if not target:
            return "ERROR: pipeline_discover butuh 'target' atau scope target."
        in_scope, scope_msg = ctx.scope_mgr.is_in_scope(target)
        if not in_scope:
            return f"ERROR: target out of scope — {scope_msg}"
        if not is_no_limits(ctx.config) and not ctx.state.scope.authorized:
            return "DITOLAK: scope belum authorized. Set scope dengan authorized=true dulu."
        if not ctx.permission_mgr.mode_sufficient(_PermissionMode.STANDARD_TESTING):
            return (
                f"DITOLAK: pipeline_discover butuh minimal preset 'ask' (standard testing); "
                f"mode sekarang {ctx.permission_mgr.mode.value}."
            )

        seed = Surface.from_target(target)
        auto = auto_approve_enabled(ctx.config)
        purpose = str(args.get("purpose") or "pipeline:discover")

        def _send(request):  # type: ignore[no-untyped-def]
            result = ctx.terminal.dispatch_tool(
                "http_probe",
                {
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers or {}),
                    "body": request.body or "",
                    "follow_redirects": bool(request.follow_redirects),
                    "purpose": purpose,
                },
                actor="recon",
                approved=approved or auto,
            )
            if result.blocked:
                return {
                    "status_code": 0,
                    "stdout": f"BLOCKED: {result.block_reason}",
                    "method": request.method,
                    "url": request.url,
                    "response_headers": {},
                    "transcript_id": result.transcript_id,
                }
            return {
                "status_code": result.status_code,
                "stdout": result.stdout,
                "method": request.method,
                "url": result.url or request.url,
                "response_headers": dict(result.response_headers or {}),
                "transcript_id": result.transcript_id,
            }

        session = ProbeSession(
            sender=_send,
            rate_limit_rps=float(getattr(ctx.state.scope, "rate_limit_rps", 0.0) or 0.0),
            scope_guard=scope_guard_from(getattr(ctx.state, "scope", None)),
        )
        baseline, html = session.baseline(seed)
        if baseline.status == 0:
            return json.dumps({
                "error": baseline.snippet or "seed fetch gagal/diblokir",
                "baseline_transcript_id": baseline.transcript_id,
            }, indent=2)

        limit = max(1, min(100, int(args.get("max_surfaces", 25) or 25)))
        seed_url = baseline.url or seed.url
        surfaces = discover_document(
            seed_url, html,
            content_type=str(baseline.headers.get("content-type", "")),
            max_surfaces=limit,
        )
        direct_scripts = external_script_urls(seed_url, html, max_urls=5)
        seen_scripts: set[str] = set()
        queue = list(direct_scripts)
        external_fetched = 0
        imported_fetched = 0
        extra: list = []
        while queue and len(seen_scripts) < 12:
            script_url = queue.pop(0)
            if script_url in seen_scripts:
                continue
            seen_scripts.add(script_url)
            script_obs, script_body = session.send(
                RequestSpec("GET", script_url), role="discover:js",
            )
            if script_obs.status != 200 or len(script_body) > 512_000:
                continue
            external_fetched += 1
            if script_url not in direct_scripts:
                imported_fetched += 1
            extra.extend(discover_from_js(script_url, script_body, max_surfaces=limit))
            for imported in js_import_urls(script_url, script_body, max_urls=5):
                if imported not in seen_scripts:
                    queue.append(imported)
        merged = []
        seen = set()
        for item in [*surfaces, *extra]:
            key = (
                item.surface.method.upper(),
                item.surface.url,
                tuple(sorted(p.name for p in item.surface.params)),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        surfaces = merged[:limit]
        data = {
            "seed_url": seed_url,
            "baseline_status": baseline.status,
            "baseline_transcript_id": baseline.transcript_id,
            "count": len(surfaces),
            "external_scripts_fetched": external_fetched,
            "imported_js_fetched": imported_fetched,
            "surfaces": [item.to_dict() for item in surfaces],
            "message": (
                "Tetap kandidat, bukan temuan. Pilih surface yang relevan lalu panggil "
                "pipeline_probe; request probe tetap melewati scope/approval."
            ),
        }
        stored = ctx.state.metadata.setdefault("discovered_surfaces", [])
        stored.extend(data["surfaces"])
        if len(stored) > 200:
            del stored[:-200]
        return json.dumps(data, indent=2)[:12000]

    if name == "pipeline_probe":
        from wth_ai.models import PermissionMode as _PermissionMode
        from wth_ai.pipeline.models import Surface
        from wth_ai.pipeline.runner import run_technique
        from wth_ai.pipeline.session import ProbeSession, scope_guard_from
        from wth_ai.pipeline.techniques import get_technique, technique_classes

        vuln_class = str(args.get("vuln_class", "") or "").strip().lower()
        technique = get_technique(vuln_class)
        if technique is None:
            return (
                f"ERROR: pipeline belum punya technique '{vuln_class}'. "
                f"Tersedia: {', '.join(sorted(set(technique_classes())))}. "
                "Kelas yang tidak punya teknik pipeline (mis. nuclei) tetap lewat jalur khusus."
            )
        target = str(args.get("target") or ctx.state.scope.target or "").strip()
        if not target:
            return "ERROR: pipeline_probe butuh 'target' atau scope target."
        in_scope, scope_msg = ctx.scope_mgr.is_in_scope(target)
        if not in_scope:
            return f"ERROR: target out of scope — {scope_msg}"
        if not is_no_limits(ctx.config) and not ctx.state.scope.authorized:
            return "DITOLAK: scope belum authorized. Set scope dengan authorized=true dulu."
        if not ctx.permission_mgr.mode_sufficient(_PermissionMode.STANDARD_TESTING):
            return (
                f"DITOLAK: pipeline_probe butuh minimal preset 'ask' (standard testing); "
                f"mode sekarang {ctx.permission_mgr.mode.value}."
            )

        surface = Surface.from_target(
            target,
            method=str(args.get("method", "GET") or "GET"),
            headers=args.get("headers") or {},
            body=str(args.get("body", "") or ""),
        )
        if not surface.params and getattr(technique, "requires_parameter", True):
            return json.dumps({
                "verified": False,
                "risk": "low",
                "message": "Tidak ada parameter query/JSON/form; tidak ada yang diprobe.",
                "params": [],
            }, indent=2)

        auto = auto_approve_enabled(ctx.config)
        purpose = str(args.get("purpose") or f"pipeline:{vuln_class}")

        def _send(request):  # type: ignore[no-untyped-def]
            started = time.perf_counter()
            result = ctx.terminal.dispatch_tool(
                "http_probe",
                {
                    "method": request.method,
                    "url": request.url,
                    "headers": dict(request.headers or {}),
                    "body": request.body or "",
                    "follow_redirects": bool(request.follow_redirects),
                    "purpose": purpose,
                },
                actor="executor",
                approved=approved or auto,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if result.blocked:
                return {
                    "status_code": 0,
                    "stdout": f"BLOCKED: {result.block_reason}",
                    "method": request.method,
                    "url": request.url,
                    "response_headers": {},
                    "elapsed_ms": elapsed_ms,
                    "transcript_id": result.transcript_id,
                }
            return {
                "status_code": result.status_code,
                "stdout": result.stdout,
                "method": request.method,
                "url": result.url or request.url,
                "response_headers": dict(result.response_headers or {}),
                "elapsed_ms": elapsed_ms,
                "transcript_id": result.transcript_id,
            }

        max_probes = int(args.get("max_probes", getattr(technique, "probe_budget", 12)) or 12)
        max_probes = max(1, min(30, max_probes))
        session = ProbeSession(
            sender=_send,
            rate_limit_rps=float(getattr(ctx.state.scope, "rate_limit_rps", 0.0) or 0.0),
            scope_guard=scope_guard_from(getattr(ctx.state, "scope", None)),
        )
        run = run_technique(
            technique, surface, session,
            max_probes=max_probes,
            deadline_s=60.0,
            context={
                "metadata": dict(ctx.state.metadata or {}),
                "state": ctx.state,
                "transcripts": ctx.terminal.transcripts,
                "tool": "pipeline_probe",
            },
        )
        if run.baseline.status == 0:
            return json.dumps({
                "verified": False,
                "risk": "low",
                "error": run.baseline.snippet or "baseline request gagal/diblokir",
                "baseline_transcript_id": run.baseline.transcript_id,
                "params": [p.to_dict() for p in surface.params],
            }, indent=2)

        if run.pairs:
            ctx.state.metadata.setdefault("request_pairs", []).extend(run.pairs)
        data = run.to_dict()
        data["message"] = (
            "Evidence di atas hanya dari verdict sinyal; needs_more_evidence bukan berarti aman."
        )
        return json.dumps(data, indent=2)

    if name == "solidity_audit":
        from wth_ai.solidity import dispatch_solidity_audit

        return dispatch_solidity_audit(ctx, args, approved=approved)

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
            # Strix-like "reason from the last response": a blocked response is
            # NOT an answer, it is an instruction. Mutate the request right
            # here (path/header/encoding/method) and re-probe -- the operator
            # does not run /adaptive; the engine runs by itself. Disable via
            # {"adaptive": "0"} on the probe args.
            if not result.blocked and str(args.get("adaptive", "1")).lower() not in {"0", "false", "no"}:
                try:
                    from urllib.parse import urlsplit

                    from wth_ai.adaptive import AdaptiveEngine, HttpRequest, classify
                    from wth_ai.terminal.analysis import evidence_pair as _pair

                    probe_url = str(args.get("url", "") or "")
                    headers = dict(getattr(result, "response_headers", {}) or {})
                    signal = classify(int(getattr(result, "status_code", 0) or 0),
                                      headers, str(result.stdout or ""), analysis=result.analysis or "")
                    if signal.is_blocked and "://" in probe_url:
                        host = urlsplit(probe_url).netloc or ctx.state.scope.target or "target"
                        engine = AdaptiveEngine(host)
                        seed = HttpRequest(
                            method=str(args.get("method", "GET")),
                            url=probe_url,
                            headers={str(k): str(v) for k, v in (args.get("headers") or {}).items()},
                            body=args.get("body") if args.get("body") is not None else None,
                        )
                        moves = int(args.get("adaptive_moves", 3) or 3)
                        attempts = engine.retry_blocked(
                            seed, result,
                            lambda p: ctx.terminal.dispatch_tool(
                                "http_probe", p, actor="supervisor",
                                approved=approved or auto),
                            limit=max(1, moves),
                        )
                        for attempt in attempts:
                            if attempt.transcript_id:
                                ctx.state.metadata.setdefault("request_pairs", []).append(
                                    _pair(_tx_stub(ctx.terminal, attempt.transcript_id),
                                          role=f"adaptive:{attempt.strategy}"))
                            parts.append(
                                f"[ADAPTIF] {attempt.strategy} ({attempt.description}) "
                                f"-> status={attempt.status or '-'} signal={attempt.signal}"
                                f"{' BYPASS' if attempt.improved else ''}"
                                f"{f' tx={attempt.transcript_id}' if attempt.transcript_id else ''}"
                                + (f"; {attempt.note[:200]}" if attempt.note else "")
                            )
                        if attempts and any(a.improved for a in attempts):
                            parts.append("[ADAPTIF] bypass: lanjutkan permintaan yang menang di atas.")
                except Exception as _adaptive_err:  # noqa: BLE001
                    parts.append(f"[ADAPTIF] engine error: {_adaptive_err}")

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
        pairs = []
        key = str(getattr(finding, "task_id", "") or finding.id)
        getter = getattr(ctx.evidence_vault, "pairs_for_finding", None)
        if callable(getter):
            pairs = getter(key)
        v = ctx.verifier.verify_with_evidence(
            finding,
            ctx.state,
            pairs=pairs,
            vuln_class=str(getattr(finding, "vuln_class", "") or "") or None,
            evidence_vault=ctx.evidence_vault,
        )
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
            metadata={"authorized": True if is_no_limits(ctx.config) else ctx.state.scope.authorized},
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
