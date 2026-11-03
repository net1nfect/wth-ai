"""Standalone recon CLI helpers (used by ``wth recon`` and slash handlers)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _latest_session_id(config: Any) -> str | None:
    try:
        from wth_ai.session_store import SessionStore

        state = SessionStore(config).load_latest()
        return state.session_id if state else None
    except Exception:
        return None


def sync_graph_after_recon(config: Any, target: str, output_dir: str) -> int:
    session_id = _latest_session_id(config)
    if not session_id or not output_dir:
        return 0
    try:
        from wth_ai.graph.asset_graph import AssetGraph
        from wth_ai.graph.recon_bridge import sync_recon_to_graph

        graph = AssetGraph(session_id)
        result = sync_recon_to_graph(output_dir, graph, target=target)
        graph.save()
        return int(result.get("nodes", 0))
    except Exception:
        return 0


def run_recon_job(
    mgr: Any,
    target: str,
    profile: str,
    *,
    realtime: bool = False,
    session_id: str | None = None,
    created_by: str = "cli",
) -> tuple[Any, Any]:
    from wth_ai.jobs.worker import WorkerEngine
    from wth_ai.recon.planner import is_valid_recon_target

    ok, why = is_valid_recon_target(target)
    if not ok:
        # Target sampah (mis. "--interval" dari salah ketik) tidak boleh pernah
        # menjadi job — dulu token seperti itu tersimpan dan ticker cron
        # menjalankannya berulang di background.
        raise ValueError(f"target recon tidak valid: {why}")

    meta: dict[str, Any] = {
        "authorized": True,
        "realtime": realtime,
        "profile": profile,
    }
    if session_id:
        meta["session_id"] = session_id
    j = mgr.create_job(
        "recon",
        target=target,
        profile=profile,
        created_by=created_by,
        metadata=meta,
    )
    if j.status == "failed":
        return j, None
    WorkerEngine(mgr).run(drain=True)
    return j, mgr.get_job(j.job_id)


def format_job_summary(j: Any, done: Any, *, graph_nodes: int = 0) -> str:
    lines = [
        f"Job {j.job_id} -> {done.status if done else j.status}",
        f"  target: {j.target}",
        f"  profile: {(j.metadata or {}).get('profile', '-')}",
    ]
    if not done:
        return "\n".join(lines)
    res = done.result or {}
    lines.append(f"  output: {res.get('output_dir', '-')}")
    summary = res.get("summary", {})
    if isinstance(summary, dict) and summary.get("assets"):
        lines.append(f"  assets: {json.dumps(summary.get('assets', {}))}")
    if res.get("priority_targets") is not None:
        lines.append(f"  priority targets: {res.get('priority_targets', 0)}")
    if graph_nodes:
        lines.append(f"  graph nodes synced: {graph_nodes}")
    report = Path(str(res.get("output_dir", ""))) / "recon_report.md"
    if report.exists():
        lines.append(f"  report: {report}")
    return "\n".join(lines)


def recon_plan_text(target: str, profile: str) -> str:
    from wth_ai.recon.planner import ReconPlanner
    from wth_ai.recon.registry import ReconToolRegistry

    plan = ReconPlanner(ReconToolRegistry()).plan(target, profile=profile)
    names = [p.tool if hasattr(p, "tool") else str(p) for p in plan.selected]
    lines = [
        f"Recon plan — {target} ({profile})",
        f"Selected ({len(names)}): " + ", ".join(names[:25]),
    ]
    if plan.skipped:
        lines.append(f"Skipped ({len(plan.skipped)}):")
        for skip in list(plan.skipped)[:8]:
            if hasattr(skip, "tool"):
                lines.append(f"  - {skip.tool}: {skip.reason}")
            elif isinstance(skip, tuple) and len(skip) >= 2:
                lines.append(f"  - {skip[0]}: {skip[1]}")
    return "\n".join(lines)


def _load_normalized(mgr: Any, job_id: str) -> dict[str, Any] | None:
    job = mgr.get_job(job_id)
    if not job or not job.result:
        return None
    path = Path(str(job.result.get("output_dir", ""))) / "normalized_result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def recon_diff_text(mgr: Any, old_id: str, new_id: str) -> str:
    old = _load_normalized(mgr, old_id)
    new = _load_normalized(mgr, new_id)
    if not old or not new:
        return "Could not load normalized_result.json for both jobs."
    from wth_ai.recon.diff import diff_results

    changes = diff_results(old, new)
    lines = [f"Recon diff {old_id} -> {new_id}", ""]
    for key, val in changes.items():
        if key in ("changed", "total_changes"):
            continue
        if isinstance(val, list) and val:
            lines.append(f"{key} ({len(val)}):")
            for item in val[:15]:
                lines.append(f"  - {item}")
    lines.append(f"Total changes: {changes.get('total_changes', 0)}")
    return "\n".join(lines)


def recon_changes_text(mgr: Any, target: str) -> str:
    jobs = mgr.list_jobs(type="recon", status="completed", limit=20)
    target_jobs = [j for j in jobs if (j.target or "") == target]
    if len(target_jobs) < 2:
        return f"Need at least 2 completed recon jobs for {target}."
    return recon_diff_text(mgr, target_jobs[1].job_id, target_jobs[0].job_id)


def recon_watch_text(target: str, profile: str, interval: str) -> str:
    from wth_ai.recon.planner import is_valid_recon_target
    from wth_ai.cron.store import CronStore
    from wth_ai.recon.realtime import WatchConfig, parse_interval, save_watch

    ok, why = is_valid_recon_target(target)
    if not ok:
        return (
            f"Ditolak: {why}.\n"
            "  Contoh: wth recon watch example.com --interval 6h"
        )
    secs = parse_interval(interval or "6h")
    cfg = WatchConfig(target=target, interval_seconds=secs, profile=profile)
    path = save_watch(cfg)
    every = f"{max(secs // 3600, 1)}h" if secs >= 3600 else f"{max(secs // 60, 1)}m"
    try:
        CronStore().add("recon", target, f"every {every}", profile=profile)
    except Exception:
        pass
    return (
        f"Recon watch enabled for {target}\n"
        f"  interval: {secs}s (~{interval or '6h'})\n"
        f"  profile: {profile}\n"
        f"  config: {path}"
    )


def recon_report_text(mgr: Any, job_id: str) -> str:
    job = mgr.get_job(job_id)
    if not job or not job.result:
        return f"No result for job {job_id}"
    report = Path(str(job.result.get("output_dir", ""))) / "recon_report.md"
    if not report.exists():
        return f"Report not found: {report}"
    text = report.read_text(encoding="utf-8", errors="replace")
    return text[:12000] + ("..." if len(text) > 12000 else "")
