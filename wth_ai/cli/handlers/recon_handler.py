"""`/recon` slash handler — job + ReconEngine inline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def handle_recon(orch: Any, args: list[str]) -> str:
    sub = args[0] if args else ""
    if sub == "plan":
        return _recon_plan(orch, args[1:])
    if sub == "show":
        return _recon_show(orch, args[1:])
    if sub == "report":
        return _recon_report(orch, args[1:])
    if sub == "watch":
        return _recon_watch(orch, args[1:])
    if sub == "diff":
        return _recon_diff(orch, args[1:])
    if sub == "changes":
        return _recon_changes(orch, args[1:])
    if sub == "quick":
        return _recon_quick(orch, args[1:])
    return _recon_run(orch, args)


def _parse_recon_args(args: list[str]) -> tuple[str, str, bool, str]:
    profile = "recon-only"
    fast = False
    interval = ""
    rest: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--fast":
            fast = True
        elif a == "--profile" and i + 1 < len(args):
            profile = args[i + 1]
            i += 1
        elif a == "--interval" and i + 1 < len(args):
            interval = args[i + 1]
            i += 1
        else:
            rest.append(a)
        i += 1
    target = ""
    if rest:
        for tok in rest:
            if re.search(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", tok) or tok.startswith("http"):
                target = tok.replace("https://", "").replace("http://", "").split("/")[0]
                break
        if not target:
            target = rest[0]
    if fast and profile == "recon-only":
        profile = "budget"
    return target, profile, fast, interval


def _job_manager(orch: Any) -> Any:
    mgr = orch._supervisor_ctx.job_manager if orch._supervisor_ctx else None
    if mgr is None:
        from wth_ai.jobs.manager import JobManager
        mgr = JobManager(orch.config)
    return mgr


def _run_recon_job(orch: Any, target: str, profile: str, *, realtime: bool = False) -> tuple[Any, Any]:
    from wth_ai.jobs.worker import WorkerEngine

    mgr = _job_manager(orch)
    j = mgr.create_job(
        "recon",
        target=target,
        profile=profile,
        created_by="repl",
        metadata={
            "authorized": True,
            "realtime": realtime,
            "profile": profile,
            "session_id": orch.state.session_id,
        },
    )
    if j.status == "failed":
        return j, None
    WorkerEngine(mgr).run(drain=True)
    done = mgr.get_job(j.job_id)
    return j, done


def _sync_graph(orch: Any, done: Any, target: str) -> int:
    output_dir = (done.result or {}).get("output_dir", "") if done else ""
    if not output_dir:
        return 0
    try:
        from wth_ai.graph.recon_bridge import sync_recon_to_graph

        sync = sync_recon_to_graph(output_dir, orch.asset_graph, target=target)
        orch.asset_graph.save()
        return int(sync.get("nodes", 0))
    except Exception:
        return 0


def _recon_run(orch: Any, args: list[str]) -> str:
    target, profile, _fast, _interval = _parse_recon_args(args)
    if not target:
        target = orch.state.scope.target or ""
    if not target:
        return "Usage: `/recon example.com` or set target first: `/target example.com`"

    ok, msg = orch.scope_mgr.is_in_scope(target)
    if not ok and orch.state.scope.target:
        return f"Blocked: {msg}"

    if not orch.state.scope.target:
        orch.scope_mgr.update_from_dict({"target": target, "in_scope": [target], "authorized": True})
        orch.state.scope = orch.scope_mgr.scope

    bus = getattr(orch, "_ui_bus", None)
    if bus:
        bus.emit("phase", phase="recon", pct=5)
        bus.emit("user", text=f"/recon {target}")

    j, done = _run_recon_job(orch, target, profile)
    if j.status == "failed":
        return f"Recon rejected: {j.error}"
    if not done:
        return f"Job {j.job_id} not found after run."

    if bus:
        bus.emit("phase", phase="idle", pct=100)

    orch.state.metadata["last_recon"] = {"job_id": j.job_id, "target": target, "profile": profile}
    orch._persist()

    graph_nodes = _sync_graph(orch, done, target)
    res = done.result or {}
    summary = res.get("summary", {})
    assets = summary.get("assets", {}) if isinstance(summary, dict) else {}
    output_dir = res.get("output_dir", "")

    lines = [
        f"## Recon complete — `{j.job_id}`",
        f"- Target: **{target}**",
        f"- Profile: {profile}",
        f"- Status: {done.status}",
    ]
    if graph_nodes:
        lines.append(f"- Graph nodes: {graph_nodes}")
    if assets:
        lines.append(f"- Assets: `{json.dumps(assets)}`")
    if output_dir:
        lines.append(f"- Output: `{output_dir}`")
        report = Path(output_dir) / "recon_report.md"
        if report.exists():
            lines.append(f"- Report: `{report}`")
    lines.append("\nNext: `/plan` or `/hunt` for vulnerability analysis.")
    return "\n".join(lines)


def _recon_quick(orch: Any, args: list[str]) -> str:
    target, _profile, _fast, _ = _parse_recon_args(args)
    if not target:
        target = orch.state.scope.target or ""
    if not target:
        return "Usage: `/recon quick example.com`"
    return _recon_run(orch, [target, "--fast"])


def _recon_watch(orch: Any, args: list[str]) -> str:
    from wth_ai.recon.realtime import WatchConfig, parse_interval, save_watch

    target, profile, _, interval = _parse_recon_args(args)
    if not target:
        target = orch.state.scope.target or ""
    if not target:
        return "Usage: `/recon watch example.com --interval 6h`"
    secs = parse_interval(interval or "6h")
    cfg = WatchConfig(target=target, interval_seconds=secs, profile=profile)
    path = save_watch(cfg)
    # Also register cron entry when cron store available
    try:
        from wth_ai.cron.store import CronStore

        store = CronStore()
        every = f"{secs // 3600}h" if secs >= 3600 else f"{secs // 60}m"
        store.add(target=target, schedule=f"every {every}", job_type="recon", profile=profile)
    except Exception:
        pass
    return (
        f"## Recon watch enabled\n"
        f"- Target: **{target}**\n"
        f"- Interval: {secs}s (~{interval or '6h'})\n"
        f"- Profile: {profile}\n"
        f"- Config: `{path}`\n"
        f"\nCron ticker will schedule recon runs automatically."
    )


def _load_normalized_for_job(mgr: Any, job_id: str) -> dict[str, Any] | None:
    job = mgr.get_job(job_id)
    if not job or not job.result:
        return None
    output_dir = job.result.get("output_dir", "")
    if not output_dir:
        return None
    path = Path(output_dir) / "normalized_result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _recon_diff(orch: Any, args: list[str]) -> str:
    if len(args) < 2:
        return "Usage: `/recon diff <job_id_a> <job_id_b>`"
    mgr = _job_manager(orch)
    old = _load_normalized_for_job(mgr, args[0])
    new = _load_normalized_for_job(mgr, args[1])
    if not old or not new:
        return "Could not load normalized_result.json for both jobs."
    from wth_ai.recon.diff import diff_results

    changes = diff_results(old, new)
    lines = [f"## Recon diff `{args[0]}` → `{args[1]}`", ""]
    for key, val in changes.items():
        if key in ("changed", "total_changes"):
            continue
        if isinstance(val, list) and val:
            lines.append(f"### {key} ({len(val)})")
            for item in val[:20]:
                lines.append(f"- {item}")
    lines.append(f"\n**Total changes:** {changes.get('total_changes', 0)}")
    return "\n".join(lines)


def _recon_changes(orch: Any, args: list[str]) -> str:
    target, _, _, _ = _parse_recon_args(args)
    if not target:
        target = orch.state.scope.target or ""
    if not target:
        return "Usage: `/recon changes example.com`"
    mgr = _job_manager(orch)
    jobs = mgr.list_jobs(type="recon", status="completed", limit=20)
    target_jobs = [j for j in jobs if (j.target or "") == target]
    if len(target_jobs) < 2:
        return f"Need at least 2 completed recon jobs for {target}."
    return _recon_diff(orch, [target_jobs[1].job_id, target_jobs[0].job_id])


def _recon_plan(orch: Any, args: list[str]) -> str:
    from wth_ai.recon.planner import ReconPlanner
    from wth_ai.recon.registry import ReconToolRegistry

    target, profile, _, _ = _parse_recon_args(args)
    if not target:
        target = orch.state.scope.target or "example.com"
    registry = ReconToolRegistry()
    planner = ReconPlanner(registry)
    plan = planner.plan(target, profile=profile)
    names = [p.tool for p in plan.selected]
    lines = [f"## Recon plan — {target} ({profile})", ""]
    lines.append(f"Selected ({len(names)}): " + ", ".join(names[:20]))
    if plan.skipped:
        lines.append(f"\nSkipped ({len(plan.skipped)}): first 5 reasons:")
        for skip in plan.skipped[:5]:
            lines.append(f"- {skip.tool}: {skip.reason}")
    return "\n".join(lines)


def _recon_show(orch: Any, args: list[str]) -> str:
    job_id = args[0] if args else orch.state.metadata.get("last_recon", {}).get("job_id", "")
    if not job_id:
        return "Usage: `/recon show <job_id>`"
    mgr = _job_manager(orch)
    job = mgr.get_job(job_id)
    if not job:
        return f"Job not found: {job_id}"
    return f"## Job {job_id}\n- status: {job.status}\n- target: {job.target}\n- result: {json.dumps(job.result or {}, indent=2)[:3000]}"


def _recon_report(orch: Any, args: list[str]) -> str:
    job_id = args[0] if args else orch.state.metadata.get("last_recon", {}).get("job_id", "")
    if not job_id:
        return "Usage: `/recon report <job_id>`"
    mgr = _job_manager(orch)
    job = mgr.get_job(job_id)
    if not job or not job.result:
        return f"No result for job {job_id}"
    output_dir = job.result.get("output_dir", "")
    if not output_dir:
        return "No output_dir in job result."
    report = Path(output_dir) / "recon_report.md"
    if not report.exists():
        return f"Report not found at {report}"
    text = report.read_text(encoding="utf-8", errors="replace")
    return text[:12000] + ("..." if len(text) > 12000 else "")
