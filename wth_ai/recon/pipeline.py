"""Legacy-compatible recon pipeline — uses wth_ai.recon.phases (not reconnaissance/)."""

from __future__ import annotations

import asyncio
from typing import Any

from wth_ai.recon.phases import PHASES


class ReconPipeline:
    """Run WTH recon phases with async orchestration."""

    PHASE_NAMES = {
        1: "subdomain_enumeration",
        2: "dns_resolution",
        3: "http_probing",
        4: "content_discovery",
        5: "parameter_mining",
        6: "js_analysis",
        7: "cloud_enum",
        8: "ssl_analysis",
        9: "fingerprinting",
        10: "tech_stack",
    }

    async def run(
        self,
        target: str,
        phases: list[int] | None = None,
        *,
        mode: str = "passive",
        threads: int = 10,
    ) -> dict[str, Any]:
        del mode, threads  # kept for API compat
        phases = phases or [1, 2, 3]
        context: dict[str, Any] = {"target": target, "observations": []}
        results = []
        for num in phases:
            if num not in PHASES:
                continue
            phase_runner = PHASES[num]()
            result = await phase_runner.run(context)  # type: ignore[arg-type]
            results.append(result)
            if result.get("observations"):
                context.setdefault("observations", []).extend(result["observations"])

        return {
            "target": target,
            "phases_run": phases,
            "results": results,
            "summary": self._summarize(results),
        }

    def run_sync(self, target: str, phases: list[int] | None = None, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.run(target, phases, **kwargs))

    def _summarize(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        total_obs = sum(len(r.get("observations", [])) for r in results)
        return {
            "phases_completed": len(results),
            "total_observations": total_obs,
            "phase_names": [r.get("name", "unknown") for r in results],
        }
