"""Default job handlers.

Handler signature: ``handler(job, ctx) -> dict``. ``ctx`` exposes
``ctx.log(str)``, ``ctx.progress(current, total, message)`` and
``ctx.should_stop()``. Fase 5 wires the real recon platform behind
``handler_recon``; executor/report use orchestrator bridge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

Handler = Callable[[Any, Any], dict[str, Any]]


def _load_baseline(store: Any, target: str, current_job_id: str) -> dict[str, Any] | None:
    """Return the normalized result of the most recent prior recon for ``target``."""
    if store is None or not target:
        return None
    try:
        prior = store.list_jobs(type="recon", status="completed", limit=50)
    except Exception:  # noqa: BLE001
        return None
    for j in prior:
        if j.job_id == current_job_id or (j.target or "") != target:
            continue
        path = Path(store.results_dir) / j.job_id / "normalized_result.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _load_fingerprint_tech_stack(output_dir: Any) -> dict[str, str]:
    """Technology -> version map for a recon job, from its normalized artifacts.

    The job summary only carries COUNTS, so the previous
    ``summary.get("fingerprints", {})`` was wrong twice over: the real records
    live under ``summary["assets"]`` (built by ``ReconNormalizer.summary()``),
    and even there ``fingerprints`` is an int, not a map of technologies. The
    actual records are written to ``normalized/fingerprint.json``.
    """
    try:
        path = Path(str(output_dir)) / "normalized" / "fingerprint.json"
        if not path.exists():
            return {}
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tech = str(row.get("technology") or "").strip()
        if tech:
            out.setdefault(tech, str(row.get("version") or ""))
    return out


def _sync_graph_after_recon(
    result: dict[str, Any],
    job: Any,
    ctx: Any,
    *,
    target: str,
) -> dict[str, Any] | None:
    """Populate session AssetGraph from recon job output."""
    output_dir = result.get("output_dir")
    if not output_dir:
        return None
    meta = getattr(job, "metadata", {}) or {}
    session_id = meta.get("session_id")
    graph_sync: dict[str, Any] | None = None
    try:
        from wth_ai.graph.asset_graph import AssetGraph
        from wth_ai.graph.recon_bridge import sync_recon_to_graph
        from wth_ai.session_store import SessionStore
        from wth_ai.config import WTHConfig

        if session_id:
            graph = AssetGraph(session_id)
            graph_sync = sync_recon_to_graph(output_dir, graph, target=target)
            store = SessionStore(WTHConfig.load())
            state = store.load(session_id)
            if state:
                state.metadata["last_recon"] = {
                    "job_id": job.job_id,
                    "target": target,
                    "graph_nodes": graph_sync.get("nodes", 0) if graph_sync else 0,
                }
                tech_stack = _load_fingerprint_tech_stack(output_dir)
                if tech_stack:
                    state.metadata["attack_surface_obj"] = {
                        "target": target,
                        "tech_stack": tech_stack,
                    }
                store.save(state)
        manager = getattr(ctx, "_manager", None)
        bus = getattr(manager, "bus", None)
        if bus:
            bus.emit(
                "recon.completed",
                job_id=job.job_id,
                data={"target": target, "output_dir": output_dir, "graph_sync": graph_sync or {}},
            )
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"graph sync warning: {exc}")
    return graph_sync


def handler_recon(job: Any, ctx: Any) -> dict[str, Any]:
    """Run the real recon engine (authorized, recon-only, non-destructive)."""
    from wth_ai.recon.engine import ReconEngine

    target = job.target or ""
    if not target:
        return {"type": "recon", "status": "failed", "error": "no target"}
    if ctx.should_stop():
        return {"type": "recon", "status": "stopped"}

    meta = getattr(job, "metadata", {}) or {}
    profile = job.profile or meta.get("profile") or "recon-only"
    realtime = bool(meta.get("realtime", False))

    # P0-1: otorisasi dibaca dari artefak, bukan dari metadata job. Sebelum ini
    # setiap pemanggil hardcode `authorized=True`, jadi scope card tidak pernah
    # diperiksa dan `wth-ai recon run <host apa pun>` langsung memindai. Tanpa kartu
    # valid, job berhenti SEBELUM engine dibuat.
    from wth_ai.scope_card import ScopeCard, ScopeCardError
    from wth_ai.workspace import wth_home

    try:
        card = ScopeCard.load(wth_home() / "scope_card.json")
    except FileNotFoundError:
        return {"type": "recon", "status": "failed",
                "error": "scope: tidak ada scope card (jalankan `wth-ai scope` dulu)"}
    except (ScopeCardError, ValueError, OSError) as exc:
        return {"type": "recon", "status": "failed",
                "error": f"scope: scope card tidak valid ({exc})"}

    manager = getattr(ctx, "_manager", None)
    bus = getattr(manager, "bus", None)
    store = getattr(manager, "store", None)
    baseline = _load_baseline(store, target, job.job_id)

    ctx.log(f"recon start target={target} profile={profile} scope_card={card.target}")
    engine = ReconEngine(scope_card=card)
    result = engine.run(
        target,
        profile=profile,
        job_id=job.job_id,
        realtime=realtime,
        progress=ctx.progress,
        log=ctx.log,
        bus=bus,
        baseline=baseline,
        # `wth-ai job stop` can now actually stop a running scan: the engine polls
        # this between tools instead of the handler checking once at entry.
        should_stop=ctx.should_stop,
    )

    if realtime:
        try:
            from wth_ai.recon.realtime import record_run

            record_run(target, job.job_id, profile=profile)
        except Exception:  # noqa: BLE001
            pass

    graph_sync = _sync_graph_after_recon(result, job, ctx, target=target)

    return {
        "type": "recon",
        "target": target,
        "status": result.get("status", "completed"),
        "profile": profile,
        # Propagate the engine's refusal reason so a blocked scan surfaces as a
        # readable job error instead of a bare "blocked".
        "reason": result.get("reason"),
        "output_dir": result.get("output_dir"),
        "summary": result.get("summary", {}),
        "priority_targets": result.get("priority_targets", 0),
        "changes": result.get("changes", {}),
        "graph_sync": graph_sync,
    }


def handler_executor(job: Any, ctx: Any) -> dict[str, Any]:
    from wth_ai.jobs.orchestrator_bridge import run_executor_job

    ctx.log("executor start")
    return run_executor_job(job, ctx)


def handler_report(job: Any, ctx: Any) -> dict[str, Any]:
    from wth_ai.jobs.orchestrator_bridge import run_report_job

    ctx.log("report start")
    return run_report_job(job, ctx)


def handler_provider_test(job: Any, ctx: Any) -> dict[str, Any]:
    from wth_ai.config import WTHConfig
    from wth_ai.llm.router import LLMProviderRouter

    provider = job.target or (getattr(job, "metadata", {}) or {}).get("provider", "openrouter")
    ctx.log(f"provider_test start provider={provider}")
    ctx.progress(10, 100, "testing provider")
    router = LLMProviderRouter(WTHConfig.load())
    result = router.test_provider(provider)
    ctx.progress(100, 100, "done")
    return {"type": "provider_test", "provider": provider, **result}


def handler_custom(job: Any, ctx: Any) -> dict[str, Any]:
    meta = getattr(job, "metadata", {}) or {}
    action = meta.get("action", "")
    if action == "echo":
        ctx.progress(100, 100, "done")
        return {"type": "custom", "status": "completed", "echo": meta}
    ctx.progress(100, 100, "done")
    return {
        "type": "custom",
        "status": "skipped",
        "note": "custom jobs require metadata.action",
        "metadata": meta,
    }


def handler_autopilot(job: Any, ctx: Any) -> dict[str, Any]:
    """Autopilot berkala: langkah uji BERBATAS, disusun lalu dijalankan lalu dipelajari.

    Gerbang yang dijaga:
    * `authorized` wajib eksplisit (fail-closed, sama seperti handler_recon);
    * opt-in destruktif DIBACA dari scope tersimpan saat job berjalan -- cron tidak
      pernah menjadi jalan pintas untuk membukanya;
    * metode pengubah data memakai aktor `executor` (butuh STANDARD_TESTING), metode
      baca memakai aktor `recon` -- sehingga sesi read-only tetap read-only.
    """
    from wth_ai.hunt.autopilot import format_report, run_autopilot

    target = job.target or ""
    if not target:
        return {"type": "autopilot", "status": "failed", "error": "no target"}
    meta = getattr(job, "metadata", {}) or {}
    if ctx.should_stop():
        return {"type": "autopilot", "status": "stopped"}

    # P1-1 (audit 2026-11-06): otorisasi dibaca dari scope card, bukan dari
    # metadata job. Sebelum ini `handler_autopilot` hanya cek metadata
    # `authorized=True` (yang ditanami `_enqueue` di cron) lalu paksa-authorize
    # scope (`set_scope(authorized=True, in_scope=[target])`) tanpa memuat
    # `scope_card.json` — melanggar invariant P0-1 (authorization harus dari
    # artifact scope card). Tanpa kartu valid, job berhenti SEBELUM bridge dibuat.
    from wth_ai.scope_card import ScopeCard, ScopeCardError
    from wth_ai.workspace import wth_home

    try:
        card = ScopeCard.load(wth_home() / "scope_card.json")
    except FileNotFoundError:
        return {"type": "autopilot", "status": "failed",
                "error": "scope: tidak ada scope card (jalankan `wth-ai scope` dulu)"}
    except (ScopeCardError, ValueError, OSError) as exc:
        return {"type": "autopilot", "status": "failed",
                "error": f"scope: scope card tidak valid ({exc})"}

    ctx.progress(10, 100, "autopilot: menyusun langkah")
    try:
        from wth_ai.config import WTHConfig
        from wth_ai.models import SessionState
        from wth_ai.permission_manager import PermissionManager
        from wth_ai.scope_manager import ScopeManager
        from wth_ai.terminal.bridge import AgentTerminalBridge
    except Exception as exc:  # noqa: BLE001
        return {"type": "autopilot", "status": "failed", "error": f"import: {exc}"}

    config = WTHConfig.load()
    session_id = meta.get("session_id") or f"autopilot-{job.job_id}"

    # PermissionManager menerima MODE, bukan config. Versi pertama gua mengirim
    # `PermissionManager(config)`, sehingga `mode` berisi objek config dan
    # `mode_sufficient()` melempar "TypeError: unhashable type: 'WTHConfig'" -- SETIAP
    # probe gagal dan hasilnya tampil sebagai status kosong (terukur di UAT CLI:
    # langkah autopilot "dijalankan" tapi nol permintaan nyata sampai ke target).
    # Mode diambil dari sesi tersimpan supaya job berjalan dengan izin yang sama
    # seperti yang operator pakai, bukan default.
    permission = PermissionManager()
    saved_scope = None
    try:
        from wth_ai.session_store import SessionStore

        saved_scope = SessionStore(config).load_latest()
    except Exception:  # noqa: BLE001
        saved_scope = None
    if saved_scope is not None:
        mode_value = getattr(saved_scope, "permission_mode", None)
        if mode_value is not None:
            try:
                permission.set_mode(getattr(mode_value, "value", mode_value))
            except Exception:  # noqa: BLE001
                pass

    # Scope bridge diisi SEBELUM bridge dibuat (bukan dimutasi sesudahnya): bridge
    # memakai ScopeManager sendiri, dan tanpa target/in-scope di sana setiap probe
    # berhenti di gate scope dengan status kosong. Nilai diambil DARI scope card
    # (P1-1), bukan hardcode — target/domain/CIDR kartu yang menentukan in-scope.
    scope_manager = ScopeManager()
    in_scope = [*(card.domains or []), *(card.cidr or [])] or [card.target]
    scope_manager.set_scope(
        target=card.target,
        authorized=card.authorized,
        in_scope=in_scope,
        destructive=card.destructive,
    )
    bridge = AgentTerminalBridge(config, permission, scope_manager, session_id)

    state = SessionState()
    state.scope.target = card.target
    state.scope.in_scope = list(in_scope)
    state.scope.authorized = card.authorized
    state.scope.destructive_allowed = card.destructive
    # Opt-in destruktif dari scope yang TERSIMPAN (hasil `/scope set destructive=yes`),
    # bukan dari metadata job -- supaya cron tidak bisa membukanya sendiri.
    try:
        from wth_ai.session_store import SessionStore

        latest = SessionStore(config).load_latest()
        if latest is not None and getattr(latest, "scope", None) is not None:
            state.scope.destructive_allowed = bool(
                getattr(latest.scope, "destructive_allowed", False)
            )
    except Exception:  # noqa: BLE001
        state.scope.destructive_allowed = False

    def _probe(probe: dict[str, Any]) -> Any:
        method = str(probe.get("method", "GET")).upper()
        actor = "recon" if method in {"GET", "HEAD", "OPTIONS"} else "executor"
        return bridge.dispatch_tool("http_probe", probe, actor=actor, approved=False)

    steps = int(meta.get("steps", 6) or 6)
    cycles = int(meta.get("cycles", 2) or 2)
    ctx.progress(30, 100, "autopilot: menjalankan langkah berbatas")
    report = run_autopilot(
        target=target, run_probe=_probe, state=state,
        max_cycles=max(1, min(6, cycles)), max_steps_per_cycle=max(1, min(20, steps)),
        destructive_allowed=state.scope.destructive_allowed,
    )
    ctx.progress(90, 100, "autopilot: menyusun usulan berikutnya")
    from wth_ai.hunt.proactive import propose_next_tests

    proposals = propose_next_tests(findings=[], request_pairs=state.metadata.get("request_pairs", []))
    ctx.progress(100, 100, "autopilot selesai")
    ctx.log(f"autopilot: {report.ran} langkah dijalankan, {report.changed} perubahan")
    return {
        "type": "autopilot",
        "status": "completed",
        "target": target,
        "report": report.to_dict(),
        "summary": format_report(report),
        "next_tests": [p.to_dict() for p in proposals],
        "destructive_allowed": state.scope.destructive_allowed,
    }


def default_handlers() -> dict[str, Handler]:
    return {
        "recon": handler_recon,
        "executor": handler_executor,
        "report": handler_report,
        "provider_test": handler_provider_test,
        "custom": handler_custom,
        "autopilot": handler_autopilot,
    }
