from __future__ import annotations

from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase03Http(BasePhase):
    phase, name, artifact = 3, "http_probing", "live-hosts.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        hosts = ctx.read_lines("resolved.txt") or ctx.surface.subdomains
        if not hosts:
            return {"phase": self.phase, "name": self.name, "count": 0}
        ctx.write_lines("_httpx_input.txt", hosts)
        lines = ctx.run_phase_tools(
            3,
            lambda s: f"-l {ctx.out_dir}/_httpx_input.txt -silent -status-code -title -tech-detect" if s.id == "httpx" else None,
        )
        merged = ctx.merge_file(self.artifact, lines)
        ctx.surface.live_hosts = [self.parse_httpx(ln) for ln in merged]
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(merged)}
