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
    # Fail-closed, matching `orchestrator_bridge.run_executor_job()`'s
    # `metadata.get("authorized", False)`. This used to default to True, which
    # meant ANY caller that forgot to pass the flag got a live scan against an
    # unconfirmed target -- and the authorization gate in `ReconEngine.run()`
    # could never fire, because nothing ever sent authorized=False. Callers that
    # represent explicit operator intent (`wth recon run/quick`, the `/recon`
    # slash handler, a `recon watch` cron entry, the supervisor's
    # `job_create_recon`) set it explicitly at that boundary.
    authorized = bool(meta.get("authorized", False))
    realtime = bool(meta.get("realtime", False))

    manager = getattr(ctx, "_manager", None)
    bus = getattr(manager, "bus", None)
    store = getattr(manager, "store", None)
    baseline = _load_baseline(store, target, job.job_id)

    ctx.log(f"recon start target={target} profile={profile} authorized={authorized}")
    engine = ReconEngine()
    result = engine.run(
        target,
        profile=profile,
        job_id=job.job_id,
        authorized=authorized,
        realtime=realtime,
        progress=ctx.progress,
        log=ctx.log,
        bus=bus,
        baseline=baseline,
        # `wth job stop` can now actually stop a running scan: the engine polls
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


def default_handlers() -> dict[str, Handler]:
    return {
        "recon": handler_recon,
        "executor": handler_executor,
        "report": handler_report,
        "provider_test": handler_provider_test,
        "custom": handler_custom,
    }
