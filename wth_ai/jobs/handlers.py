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

    # Unified multi-format report (Fase 6 Stage 9) alongside recon artifacts.
    try:
        from wth_ai.reporting import build_report
        out = result.get("output_dir")
        if out:
            build_report(out, target=target)
    except Exception as exc:  # noqa: BLE001 — reporting never breaks recon
        ctx.log(f"report build skipped: {exc}")

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


def _load_normalized(store: Any, recon_job_id: str | None) -> dict[str, Any] | None:
    if not store or not recon_job_id:
        return None
    path = Path(store.results_dir) / recon_job_id / "normalized_result.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def handler_executor(job: Any, ctx: Any) -> dict[str, Any]:
    """Run the gated, non-destructive active Execution Engine (Fase 6 Stage 5/6)."""
    from wth_ai.executor.engine import ExecutionEngine
    from wth_ai.planning import TestPlanner
    from wth_ai.recon import http as rhttp

    target = job.target or ""
    if not target:
        return {"type": "executor", "status": "failed", "error": "no target"}
    if ctx.should_stop():
        return {"type": "executor", "status": "stopped"}

    meta = getattr(job, "metadata", {}) or {}
    mode = meta.get("execution_mode") or "safe-active"
    profile = job.profile or meta.get("profile") or "recon-only"
    request_budget = int(meta.get("request_budget") or 200)
    rate_rps = float(meta.get("rate_rps") or 2.0)
    credentials = meta.get("credentials") or {}

    manager = getattr(ctx, "_manager", None)
    bus = getattr(manager, "bus", None)
    store = getattr(manager, "store", None)

    # 1) obtain normalized recon data (reuse a prior recon, else run one) -----
    ctx.progress(5, 100, "load recon baseline")
    normalized = _load_normalized(store, meta.get("recon_job_id"))
    if normalized is None:
        ctx.log("no recon baseline supplied — running a recon-only pass first")
        from wth_ai.recon.engine import ReconEngine
        recon = ReconEngine().run(target, profile="recon-only", job_id=f"{job.job_id}-recon",
                                  authorized=True, log=ctx.log)
        normalized = recon.get("normalized", {})

    # 2) plan execution manifest ---------------------------------------------
    ctx.progress(15, 100, "test planning")
    has_creds = bool(credentials.get("cookie") or credentials.get("authorization"))
    manifest = TestPlanner().build(
        target, normalized, mode=mode, request_budget=request_budget,
        has_credentials=has_creds,
    )
    out_dir = Path(store.results_dir) / job.job_id if store else Path(f"./{job.job_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "exec_manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    ctx.log(f"manifest: {len(manifest.tasks)} tasks (mode={mode}, budget={request_budget})")

    # 3) run engine ----------------------------------------------------------
    scope_hosts = {rhttp.host_of(target)}
    for sub in normalized.get("subdomains", []) or []:
        if sub.get("host"):
            scope_hosts.add(sub["host"].lower())

    # high-sensitivity tasks only proceed when the engagement was approved for
    # mutation (approval-required mode is parked for human approval at creation).
    from wth_ai.policy.execution_mode import capability
    cap = capability(mode)
    approve = (lambda _t: True) if cap.allow_mutation else (lambda _t: False)

    engine = ExecutionEngine(scope_hosts=scope_hosts, rate_rps=rate_rps)
    result = engine.run(
        manifest, credentials=credentials, progress=ctx.progress, log=ctx.log,
        bus=bus, job_id=job.job_id, approve=approve,
    )

    # 4) response analysis + triage (Fase 6 Stage 7/8) -----------------------
    ctx.progress(95, 100, "analysis & triage")
    from wth_ai.analysis import analyze_exec_results
    from wth_ai.triage import TriageEngine

    all_findings = list(result["findings"]) + analyze_exec_results(result["results"])
    triage = TriageEngine(scope_hosts=scope_hosts).triage(
        all_findings, bus=bus, job_id=job.job_id)

    # 5) persist artifacts ---------------------------------------------------
    with (out_dir / "exec_runs.jsonl").open("w", encoding="utf-8") as fh:
        for r in result["results"]:
            fh.write(json.dumps(r) + "\n")
    (out_dir / "exec_findings.json").write_text(
        json.dumps(result["findings"], indent=2), encoding="utf-8")
    (out_dir / "triage_findings.json").write_text(
        json.dumps(triage, indent=2), encoding="utf-8")
    (out_dir / "evidence_bundles.json").write_text(
        json.dumps([f["evidence_bundle"] for f in triage["findings"]], indent=2), encoding="utf-8")

    # unified report (Fase 6 Stage 9)
    try:
        from wth_ai.reporting import build_report
        build_report(out_dir, target=target)
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"report build skipped: {exc}")

    ctx.progress(100, 100, "execution complete")
    ctx.log(f"triage: {triage['count']} findings ({triage['severity_counts']})")
    if bus is not None:
        try:
            bus.emit("report.ready", job_id=job.job_id, data={"target": target, "type": "executor"})
        except Exception:  # noqa: BLE001
            pass
    return {
        "type": "executor",
        "target": target,
        "status": "completed",
        "mode": mode,
        "output_dir": str(out_dir),
        "stats": result["stats"],
        "findings": len(result["findings"]),
        "triaged": triage["count"],
        "severity_counts": triage["severity_counts"],
        "recovery": result["recovery"],
    }


def handler_report(job: Any, ctx: Any) -> dict[str, Any]:
    """Build a unified multi-format report from a source job's artifacts."""
    from wth_ai.reporting import build_report

    meta = getattr(job, "metadata", {}) or {}
    manager = getattr(ctx, "_manager", None)
    store = getattr(manager, "store", None)
    source_job_id = meta.get("source_job_id")
    target = job.target or ""

    # resolve the source job dir (explicit, else most recent completed job for target)
    if not source_job_id and store is not None:
        try:
            jobs = store.list_jobs(status="completed", limit=50)
            for j in jobs:
                if j.type in ("recon", "executor") and (not target or (j.target or "") == target):
                    source_job_id = j.job_id
                    break
        except Exception:  # noqa: BLE001
            source_job_id = None

    if not source_job_id or store is None:
        ctx.progress(100, 100, "no source job")
        return {"type": "report", "status": "failed", "error": "no source job to report on"}

    src_dir = Path(store.results_dir) / source_job_id
    ctx.progress(40, 100, "building report")
    formats = tuple(meta.get("formats") or ("json", "md", "html"))
    written = build_report(src_dir, target=target, formats=formats)
    ctx.progress(100, 100, "report ready")
    bus = getattr(manager, "bus", None)
    if bus is not None:
        try:
            bus.emit("report.ready", job_id=job.job_id,
                     data={"target": target, "source": source_job_id})
        except Exception:  # noqa: BLE001
            pass
    return {"type": "report", "status": "completed", "source_job_id": source_job_id,
            "paths": written}


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
