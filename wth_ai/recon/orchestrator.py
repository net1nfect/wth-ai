"""10-phase async recon orchestrator with adaptive graph + tool chains."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wth_ai.config import WTHConfig
from wth_ai.models import AttackSurface
from wth_ai.permission_manager import PermissionManager
from wth_ai.recon.graph import AdaptiveReconGraph
from wth_ai.recon.phases import PHASES, PhaseContext
from wth_ai.scope_manager import ScopeManager
from wth_ai.tools.doctor import ToolDoctor
from wth_ai.tools.pipeline import ToolChainOrchestrator
from wth_ai.tools.registry import ToolRegistry
from wth_ai.tools.runner import ManifestToolRunner


class ReconOrchestrator:
    """Run recon phases with adaptive graph, tool chains, and parallel groups."""

    def __init__(self, config: WTHConfig | None = None, *, output_root: str = "recon") -> None:
        self.config = config or WTHConfig.load()
        self.output_root = Path(output_root)
        self.registry = ToolRegistry()
        self.doctor = ToolDoctor(self.config)
        self.graph = AdaptiveReconGraph(self.config.recon.rules_file)
        self.chains = ToolChainOrchestrator(self.registry)

    def _normalize_host(self, target: str) -> str:
        parsed = urlparse(target if "://" in target else f"https://{target}")
        return (parsed.hostname or "").lower()

    async def run(
        self,
        target: str,
        *,
        fast: bool = False,
        focus: str | None = None,
        rate_limit_rps: float | None = None,
        auto_install: bool = True,
        phases: list[int] | None = None,
        terminal_bridge: Any = None,
    ) -> dict[str, Any]:
        host = self._normalize_host(target)
        if not host:
            return {"target": target, "error": "Invalid target", "summary": {}}

        if auto_install:
            self.doctor.ensure_recon_tools(phases or list(range(1, 6)))

        out_dir = self.output_root / host
        out_dir.mkdir(parents=True, exist_ok=True)
        rps = rate_limit_rps or self.config.tools.default_rate_limit_rps

        perm = PermissionManager()
        scope = ScopeManager()
        runner = ManifestToolRunner(self.config, perm, scope, self.registry)
        surface = AttackSurface(target=host)
        ctx = PhaseContext(host, out_dir, runner, self.registry, surface, fast=fast, rate_limit_rps=rps, terminal_bridge=terminal_bridge)

        # Tool chains first — fast piped recon
        chain_results = self.chains.run_all(host, f"https://{host}")
        self.chains.apply_to_surface(chain_results, surface)
        chain_warnings = [
            f"{name}: rc={r.returncode}" for name, r in chain_results.items() if r.returncode != 0
        ]

        phase_nums = phases
        graph_plan: dict[str, Any] = {}
        if phase_nums is None:
            # Run phases 1-3 + 9 first for fingerprint, then adaptive graph
            bootstrap = [1, 2, 3, 9]
            results: list[dict[str, Any]] = []
            for p in bootstrap:
                if p in PHASES:
                    r = await PHASES[p]().run(ctx)
                    results.append(r)

            if self.config.recon.adaptive_graph:
                graph_plan = self.graph.plan_phases(surface)
                phase_nums = self.graph.reorder_for_boost(graph_plan["phases"], graph_plan.get("boost_phases", []))
                # Add remaining phases not in bootstrap
                phase_nums = [p for p in phase_nums if p not in bootstrap]
            else:
                phase_nums = [n for n in range(1, 13) if n not in bootstrap]
        else:
            results = []

        warnings: list[str] = list(chain_warnings)

        if phase_nums is None:
            phase_nums = list(range(1, 13))

        # Sequential early phases if not bootstrapped
        if not graph_plan:
            for p in [n for n in (1, 2, 3) if n in phase_nums]:
                phase_cls = PHASES[p]
                r = await phase_cls().run(ctx)
                results.append(r)
                warnings.extend(ctx.warnings)
            phase_nums = [n for n in phase_nums if n not in (1, 2, 3)]

        # Parallel group A: 4, 5
        parallel_a = [n for n in (4, 5) if n in phase_nums]
        if parallel_a:
            rs = await asyncio.gather(*[PHASES[n]().run(ctx) for n in parallel_a])
            results.extend(rs)

        # Parallel group B: 6, 7, 8
        parallel_b = [n for n in (6, 7, 8) if n in phase_nums]
        if parallel_b:
            rs = await asyncio.gather(*[PHASES[n]().run(ctx) for n in parallel_b])
            results.extend(rs)

        # Sequential: 9 → 10 → 11 → 12 (if not already run)
        for p in [n for n in (9, 10, 11, 12) if n in phase_nums]:
            if any(r.get("phase") == p for r in results):
                continue
            r = await PHASES[p]().run(ctx)
            results.append(r)

        warnings.extend(ctx.warnings)
        summary = surface.summary()
        summary["phases_completed"] = len(results)
        summary["thin_surface"] = (
            summary.get("live_hosts", 0) <= 1
            and not summary.get("api_endpoints")
            and not summary.get("nuclei")
        )
        if graph_plan:
            summary["adaptive_profile"] = graph_plan.get("profile")
            summary["matched_rules"] = graph_plan.get("matched_rules", [])
            summary["skipped_phases"] = graph_plan.get("skip_phases", [])

        return {
            "target": host,
            "artifacts_dir": str(out_dir),
            "attack_surface": surface,
            "results": results,
            "warnings": warnings,
            "summary": summary,
            "focus": focus,
            "graph_plan": graph_plan,
            "chain_results": {k: {"rc": v.returncode, "parsed_keys": list(v.parsed.keys())} for k, v in chain_results.items()},
        }

    def run_sync(self, target: str, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.run(target, **kwargs))
