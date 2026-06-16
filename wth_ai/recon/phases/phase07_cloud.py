from __future__ import annotations

from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase07Cloud(BasePhase):
    phase, name, artifact = 7, "cloud_enum", "cloud.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        host = ctx.target
        lines = ctx.run_phase_tools(7, lambda s: host if s.id in {"cloud_enum", "s3scanner"} else None)
        merged = ctx.merge_file(self.artifact, lines)
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(merged)}
