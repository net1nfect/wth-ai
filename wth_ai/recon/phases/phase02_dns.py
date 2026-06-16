from __future__ import annotations

from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase02Dns(BasePhase):
    phase, name, artifact = 2, "dns_resolution", "resolved.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        subs = ctx.surface.subdomains or ctx.read_lines("subdomains.txt")
        if not subs:
            return {"phase": self.phase, "name": self.name, "count": 0}
        ctx.write_lines("_dns_input.txt", subs)
        lines = ctx.run_phase_tools(2, lambda s: f"-l {ctx.out_dir}/_dns_input.txt -silent" if s.id == "dnsx" else None)
        if not lines:
            lines = subs
        merged = ctx.merge_file(self.artifact, lines)
        return {"phase": self.phase, "name": self.name, "count": len(merged)}
