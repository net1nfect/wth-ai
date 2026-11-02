"""Session-scoped recon bridge for agent tool-by-tool execution."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from wth_ai.graph.adapters import normalized_to_asset_graph
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.recon import http as rhttp
from wth_ai.recon import report as report_mod
from wth_ai.recon import scoring
from wth_ai.recon.model import ToolRun
from wth_ai.recon.normalizer import ReconNormalizer
from wth_ai.recon.planner import ReconPlanner
from wth_ai.recon.registry import ReconToolRegistry
from wth_ai.recon.runner import SafeToolRunner
from wth_ai.recon.tools.builtin import BuiltinContext
from wth_ai.recon.tools.catalog import BUILTIN_TOOLS
from wth_ai.workspace import wth_home


class ReconAgentBridge:
    """Incremental recon per tool call — mirrors ReconEngine artifacts per session."""

    def __init__(
        self,
        session_id: str,
        *,
        profile: str = "recon-only",
        registry: ReconToolRegistry | None = None,
        graph: AssetGraph | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile = profile
        self.registry = registry or ReconToolRegistry()
        self.planner = ReconPlanner(self.registry)
        self.out_dir = wth_home() / "sessions" / session_id / "recon"
        self.raw_dir = self.out_dir / "raw"
        self.normalized_dir = self.out_dir / "normalized"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(exist_ok=True)
        self.normalized_dir.mkdir(exist_ok=True)
        self.runlog_path = self.out_dir / "tool_runs.jsonl"
        self.runner = SafeToolRunner(
            self.registry, raw_dir=self.raw_dir, timeout=60, runlog_path=self.runlog_path,
        )
        self.normalizer = ReconNormalizer()
        self.tool_runs: list[ToolRun] = []
        self._target = ""
        self._ctx: BuiltinContext | None = None
        self._graph = graph

    def set_target(self, target: str) -> None:
        self._target = target.strip()
        host = rhttp.host_of(self._target)
        base_url = rhttp.normalize_url(self._target)
        self._ctx = BuiltinContext(target=self._target, host=host, base_url=base_url)

    def list_tools(self, *, category: str | None = None, available_only: bool = False) -> list[dict[str, Any]]:
        specs = self.registry.tools.values()
        if category:
            specs = [s for s in specs if s.category == category]
        out: list[dict[str, Any]] = []
        for spec in sorted(specs, key=lambda s: s.name):
            if available_only and not self.registry.is_available(spec.name):
                continue
            ok, reason = self.planner._eligible(spec.name, self.profile, None)
            out.append({
                "name": spec.name,
                "category": spec.category,
                "execution_mode": spec.execution_mode,
                "risk_level": spec.risk_level,
                "available": self.registry.is_available(spec.name),
                "enabled": self.registry.is_enabled(spec.name),
                "eligible": ok,
                "reason": reason if not ok else "",
                "notes": spec.notes[:120],
            })
        return out[:40]

    def plan_preview(self, target: str) -> dict[str, Any]:
        self.set_target(target)
        plan = self.planner.plan(target, profile=self.profile)
        (self.out_dir / "recon_plan.json").write_text(
            json.dumps(plan.to_dict(), indent=2), encoding="utf-8",
        )
        return plan.to_dict()

    def run_tool(self, tool_name: str, target: str | None = None) -> dict[str, Any]:
        if target:
            self.set_target(target)
        if not self._target or not self._ctx:
            return {"status": "failed", "error": "target not set"}
        spec = self.registry.get(tool_name)
        if spec is None:
            return {"status": "failed", "error": f"unknown tool: {tool_name}"}
        ok, reason = self.planner._eligible(tool_name, self.profile, None)
        if not ok:
            return {"status": "skipped", "error": reason}
        started = time.time()
        run = self.runner.run(spec, target=self._target, ctx=self._ctx)
        self.tool_runs.append(run)
        self.normalizer.add_run(run)
        if self._graph is not None:
            normalized_to_asset_graph(self.normalizer, self._graph, target=self._ctx.host)
        duration_ms = int((time.time() - started) * 1000)
        return {
            "tool": tool_name,
            "status": run.status,
            "duration_ms": duration_ms,
            "items": len(run.normalized_items),
            "summary": self._summarize_run(run),
            "error": run.error,
        }

    def _summarize_run(self, run: ToolRun) -> str:
        kinds: dict[str, int] = {}
        for item in run.normalized_items:
            k = item.get("kind", "unknown")
            kinds[k] = kinds.get(k, 0) + 1
        if not kinds:
            return run.status
        return ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))

    def finalize(self) -> dict[str, Any]:
        if not self._target:
            return {"status": "failed", "error": "no target"}
        plan = self.planner.plan(self._target, profile=self.profile)
        self.normalizer.write_normalized(self.normalized_dir)
        priority = scoring.score(self.normalizer)
        scoring.write_priority(
            priority, self.normalized_dir / "priority_targets.json", self.out_dir / "priority_targets.csv",
        )
        (self.out_dir / "priority_targets.json").write_text(
            json.dumps([t.to_dict() for t in priority], indent=2), encoding="utf-8",
        )
        summary = report_mod.write_report(
            self.out_dir, self._target, plan, self.normalizer, priority, self.tool_runs,
        )
        (self.out_dir / "normalized_result.json").write_text(
            json.dumps(self.normalizer.to_dict(), indent=2), encoding="utf-8",
        )
        if self._graph is not None:
            normalized_to_asset_graph(self.normalizer, self._graph, target=self._ctx.host if self._ctx else self._target)
        return {
            "status": "success",
            "output_dir": str(self.out_dir),
            "summary": summary.get("assets", self.normalizer.summary()),
            "priority_targets": len(priority),
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "target": self._target,
            "profile": self.profile,
            "tool_runs": len(self.tool_runs),
            "summary": self.normalizer.summary(),
            "output_dir": str(self.out_dir),
        }

    def live_probe_high_value(self, bridge: Any, *, limit: int = 15) -> dict[str, Any]:
        """Phase-12 style http_probe on high-value graph endpoints (non-destructive)."""
        if not self._target or self._graph is None or bridge is None:
            return {"status": "skipped", "probed": 0, "reason": "missing target/graph/bridge"}

        from wth_ai.graph.models import NodeType

        urls: list[str] = []
        for node in self._graph.nodes.values():
            if node.type == NodeType.PARAMETER and node.param:
                if node.param.lower() in ("id", "user_id", "url", "redirect", "q", "search"):
                    base = node.url or f"https://{self._ctx.host if self._ctx else self._target}"
                    urls.append(f"{base}?{node.param}=probe")
            if node.type == NodeType.ENDPOINT and "/api/" in (node.label or ""):
                urls.append(node.label if node.label.startswith("http") else f"https://{self._target}{node.label}")

        probed = 0
        transcripts: list[str] = []
        for url in list(dict.fromkeys(urls))[:limit]:
            if not url.startswith("http"):
                url = f"https://{self._target}{url if url.startswith('/') else '/' + url}"
            result = bridge.http_probe("GET", url, actor="recon", purpose="live_probe_high_value")
            transcripts.append(f"[{result.transcript_id}] {result.status_code or result.exit_code} {url[:100]}")
            probed += 1
        return {"status": "success", "probed": probed, "transcripts": transcripts}
