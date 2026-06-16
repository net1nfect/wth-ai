from __future__ import annotations

from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase08Ssl(BasePhase):
    phase, name, artifact = 8, "ssl_analysis", "ssl.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        host = ctx.target
        lines = ctx.run_phase_tools(8, lambda s: f"{host}" if s.id in {"testssl", "sslscan", "sslyze"} else None)
        merged = ctx.merge_file(self.artifact, lines)
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(merged)}
