"""ReconEngine — the real recon platform (Fase 5).

Pipeline:
    policy/scope -> plan -> run builtins + safe externals -> normalize ->
    scoring -> diff -> report -> events

All recon is authorized-only, recon-only and non-destructive. The engine writes
a full artifact tree under ``$WTH_HOME/wth-results/<job_id>/`` and reports
progress 0..100 through an optional callback (wired to the job worker).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from wth_ai.recon import http as rhttp
from wth_ai.recon import report as report_mod
from wth_ai.recon import scoring
from wth_ai.recon.adaptive import (
    filter_specs_by_adaptive,
    signals_from_normalizer,
    sort_specs_by_adaptive,
)
from wth_ai.recon.diff import diff_results
from wth_ai.recon.model import ToolRun, ToolSpec
from wth_ai.recon.normalizer import ReconNormalizer
from wth_ai.recon.planner import ReconPlanner, detect_target_type
from wth_ai.recon.registry import ReconToolRegistry
from wth_ai.recon.runner import SafeToolRunner
from wth_ai.recon.tools.builtin import BuiltinContext, run_builtin
from wth_ai.recon.tools.catalog import BUILTIN_TOOLS
from wth_ai.workspace import wth_home

# builtin key -> catalog tool name (reverse of BUILTIN_TOOLS)
_KEY_TO_NAME = {v: k for k, v in BUILTIN_TOOLS.items()}
# sensible builtin execution order (data deps + monotonic progress marks)
_BUILTIN_ORDER = ["dns", "http", "headers", "tls", "robots", "crawler", "js", "params", "cloud"]

NoOp = lambda *a, **k: None  # noqa: E731


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(target: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", target)[:40]


class ReconEngine:
    def __init__(
        self,
        registry: ReconToolRegistry | None = None,
        *,
        results_root: Path | None = None,
        tool_timeout: int = 60,
    ) -> None:
        self.registry = registry or ReconToolRegistry()
        self.planner = ReconPlanner(self.registry)
        self.results_root = results_root or (wth_home() / "wth-results")
        self.tool_timeout = tool_timeout

    # ----------------------------------------------------------------- #
    def run(
        self,
        target: str,
        *,
        profile: str = "recon-only",
        job_id: str | None = None,
        authorized: bool = True,
        realtime: bool = False,
        progress: Callable[[int, int, str], None] | None = None,
        log: Callable[[str], None] | None = None,
        bus: Any = None,
        baseline: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        progress = progress or NoOp
        log = log or NoOp

        job_id = job_id or f"recon-{_slug(target)}-{int(time.time())}"
        out_dir = self.results_root / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = out_dir / "raw"
        normalized_dir = out_dir / "normalized"
        (out_dir / "screenshots").mkdir(exist_ok=True)
        runlog_path = out_dir / "tool_runs.jsonl"

        # 1) policy / scope ------------------------------------------------
        progress(5, 100, "scope/policy")
        ttype = detect_target_type(target)
        if not authorized:
            from wth_ai.engagement.mode import is_unrestricted
            if not (is_unrestricted() and os.environ.get("WTH_UNRESTRICTED") == "1"):
                log("recon blocked: target not authorized")
                result = {
                    "type": "recon", "target": target, "status": "blocked",
                    "reason": "authorized=no (recon-only requires authorization)",
                    "output_dir": str(out_dir),
                }
                (out_dir / "recon_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                return result
        if ttype == "local" and profile != "local":
            log(f"note: local target '{target}' — forcing profile=local")
            profile = "local"

        # 2) plan ----------------------------------------------------------
        progress(10, 100, "tool plan")
        plan_signals = baseline if baseline else None
        plan = self.planner.plan(target, profile=profile, realtime=realtime, signals=plan_signals)
        (out_dir / "recon_plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        log(f"plan: {len(plan.selected)} tools selected, {len(plan.skipped)} skipped (profile={profile})")
        if plan.adaptive_profile:
            log(f"adaptive profile: {plan.adaptive_profile} rules={plan.matched_rules}")

        runner = SafeToolRunner(
            self.registry, raw_dir=raw_dir, timeout=self.tool_timeout, runlog_path=runlog_path
        )
        host = rhttp.host_of(target)
        base_url = rhttp.normalize_url(target)
        ctx = BuiltinContext(target=target, host=host, base_url=base_url)
        normalizer = ReconNormalizer()
        tool_runs: list[ToolRun] = []

        selected_names = {p.tool for p in plan.selected}
        builtin_specs = [s for s in self.registry.tools.values()
                         if s.name in selected_names and s.execution_mode == "builtin"]
        external_specs = [s for s in self.registry.tools.values()
                          if s.name in selected_names and s.execution_mode in ("external", "api")]

        def _record(run: ToolRun) -> None:
            tool_runs.append(run)
            normalizer.add_run(run)

        # 3) builtin pipeline (ordered) -----------------------------------
        ordered_builtins = sorted(
            builtin_specs,
            key=lambda s: _BUILTIN_ORDER.index(BUILTIN_TOOLS.get(s.name, "zzz"))
            if BUILTIN_TOOLS.get(s.name) in _BUILTIN_ORDER else 99,
        )
        stage_marks = {"http": 35, "dns": 20, "tls": 50, "crawler": 65, "params": 75, "cloud": 85}
        for spec in ordered_builtins:
            key = BUILTIN_TOOLS.get(spec.name, "")
            run = runner.run(spec, target=target, ctx=ctx)
            _record(run)
            if key in stage_marks:
                progress(stage_marks[key], 100, f"builtin:{key}")
            log(f"builtin {spec.name}: {run.status} ({len(run.normalized_items)} items)")

        # builtin fingerprint (no catalog name; always runs, cheap)
        fp_run = self._run_fingerprint(ctx, raw_dir, runlog_path)
        _record(fp_run)

        # 4) external/api tools (availability-gated; builtins already covered
        #    the 20..85 milestones, so externals run within the 88..90 band) ---
        progress(88, 100, "external/enrichment tools")
        post_match = self.planner.refine_match(signals_from_normalizer(normalizer))
        if post_match.matched_rules:
            plan.adaptive_profile = post_match.profile
            plan.matched_rules = post_match.matched_rules
            plan.boost_categories = list(post_match.boost_categories)
            plan.skip_categories = list(post_match.skip_categories)
            (out_dir / "recon_plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
            log(f"adaptive refine: profile={post_match.profile} rules={post_match.matched_rules}")
        external_specs = filter_specs_by_adaptive(external_specs, post_match)
        external_specs = sort_specs_by_adaptive(external_specs, post_match)
        for spec in external_specs:
            run = runner.run(spec, target=target, ctx=ctx)
            _record(run)
            if run.status not in ("skipped",):
                log(f"external {spec.name}: {run.status} ({len(run.normalized_items)} items)")
        progress(90, 100, "enrichment done")

        # 5) normalize / scoring / diff -----------------------------------
        progress(92, 100, "normalize/scoring/diff")
        normalizer.write_normalized(normalized_dir)
        self._write_asset_graph(normalized_dir, normalizer)

        priority = scoring.score(normalizer)
        scoring.write_priority(
            priority, normalized_dir / "priority_targets.json", out_dir / "priority_targets.csv"
        )
        # also expose at job root for convenience
        (out_dir / "priority_targets.json").write_text(
            json.dumps([t.to_dict() for t in priority], indent=2), encoding="utf-8"
        )

        new_dict = normalizer.to_dict()
        changes: dict[str, Any] = {}
        if baseline:
            changes = diff_results(baseline, new_dict)

        # 6) report --------------------------------------------------------
        summary = report_mod.write_report(out_dir, target, plan, normalizer, priority, tool_runs, changes)
        (out_dir / "normalized_result.json").write_text(json.dumps(new_dict, indent=2), encoding="utf-8")

        # 7) events --------------------------------------------------------
        self._emit_events(bus, job_id, target, normalizer, priority, changes)
        progress(100, 100, "report")
        log(f"recon complete: {summary['assets']}")

        return {
            "type": "recon",
            "target": target,
            "status": "completed",
            "profile": profile,
            "job_id": job_id,
            "output_dir": str(out_dir),
            "summary": summary,
            "priority_targets": len(priority),
            "changes": changes,
            "normalized": new_dict,
        }

    # ----------------------------------------------------------------- #
    def _run_fingerprint(self, ctx: BuiltinContext, raw_dir: Path, runlog: Path) -> ToolRun:
        start = time.time()
        run = ToolRun(tool="builtin-fingerprint", started_at=_now())
        try:
            items = run_builtin("fingerprint", ctx)
            run.normalized_items = items
            run.status = "success"
            try:
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / "builtin-fingerprint.txt").write_text(json.dumps(items, indent=2), encoding="utf-8")
                run.output_path = str(raw_dir / "builtin-fingerprint.txt")
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error = str(exc)
        run.finished_at = _now()
        run.duration_ms = int((time.time() - start) * 1000)
        try:
            with runlog.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"tool": run.tool, "status": run.status, "items": len(run.normalized_items)}) + "\n")
        except OSError:
            pass
        return run

    def _write_asset_graph(self, normalized_dir: Path, normalizer: ReconNormalizer) -> None:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for sub in normalizer.subdomains.values():
            nodes.append({"id": sub.host, "type": "host"})
        for probe in normalizer.http_probes.values():
            nodes.append({"id": probe.url, "type": "url", "status": probe.status})
        for ep in normalizer.endpoints.values():
            nodes.append({"id": ep.url, "type": "endpoint", "kind": ep.kind})
        for p in normalizer.params.values():
            nodes.append({"id": f"{p.url}?{p.name}", "type": "parameter", "interest": p.interest})
            edges.append({"from": p.url, "to": f"{p.url}?{p.name}", "type": "param_of"})
        graph = {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}
        (normalized_dir / "asset_graph.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")

    def _emit_events(
        self, bus: Any, job_id: str, target: str, normalizer: ReconNormalizer,
        priority: list[Any], changes: dict[str, Any],
    ) -> None:
        if bus is None:
            return
        try:
            for sub in list(normalizer.subdomains.values())[:50]:
                bus.emit("recon.new_asset", job_id=job_id, data={"asset": sub.host, "kind": "subdomain"})
            for ep in list(normalizer.endpoints.values())[:50]:
                bus.emit("recon.new_endpoint", job_id=job_id, data={"endpoint": ep.url, "kind": ep.kind})
            if changes.get("changed"):
                bus.emit("recon.change_detected", job_id=job_id, data={"changes": {
                    k: v for k, v in changes.items() if isinstance(v, list) and v
                }})
            bus.emit("recon.report_ready", job_id=job_id, data={"target": target})
            bus.emit("report.ready", job_id=job_id, data={"target": target, "type": "recon"})
        except Exception:  # noqa: BLE001 — events must never break recon
            pass
