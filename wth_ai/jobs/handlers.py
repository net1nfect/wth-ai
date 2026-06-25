"""Default job handlers.

Handler signature: ``handler(job, ctx) -> dict``. ``ctx`` exposes
``ctx.log(str)``, ``ctx.progress(current, total, message)`` and
``ctx.should_stop()``. Fase 5 wires the real recon platform behind
``handler_recon`` while keeping the other handlers as safe placeholders.
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
    authorized = bool(meta.get("authorized", True))
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
    )

    # Persist watch bookkeeping when this run is part of a watch.
    if realtime:
        try:
            from wth_ai.recon.realtime import record_run

            record_run(target, job.job_id, profile=profile)
        except Exception:  # noqa: BLE001
            pass

    # Return a slim, JSON-serializable result (full data lives in wth-results/).
    return {
        "type": "recon",
        "target": target,
        "status": result.get("status", "completed"),
        "profile": profile,
        "output_dir": result.get("output_dir"),
        "summary": result.get("summary", {}),
        "priority_targets": result.get("priority_targets", 0),
        "changes": result.get("changes", {}),
    }


def handler_executor(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("executor (mock, non-destructive)")
    ctx.progress(100, 100, "done")
    return {"type": "executor", "target": job.target, "executed": False, "note": "mock non-destructive"}


def handler_report(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("report (mock)")
    ctx.progress(100, 100, "done")
    return {"type": "report", "path": None, "summary": "mock report"}


def handler_provider_test(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log(f"provider_test (mock) provider={job.target}")
    ctx.progress(100, 100, "done")
    return {"type": "provider_test", "provider": job.target, "ok": True, "note": "mock"}


def handler_custom(job: Any, ctx: Any) -> dict[str, Any]:
    ctx.log("custom job (mock)")
    ctx.progress(100, 100, "done")
    return {"type": "custom", "echo": job.metadata}


def default_handlers() -> dict[str, Handler]:
    return {
        "recon": handler_recon,
        "executor": handler_executor,
        "report": handler_report,
        "provider_test": handler_provider_test,
        "custom": handler_custom,
    }
