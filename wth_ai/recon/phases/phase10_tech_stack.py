from __future__ import annotations

from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase10TechStack(BasePhase):
    phase, name, artifact = 10, "tech_stack", "tech.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        tech: dict[str, str] = {}
        for h in ctx.surface.live_hosts:
            if h.title:
                tech[h.url] = h.title
            if h.tech:
                tech[h.url] = ", ".join(h.tech)
        lines = ctx.run_phase_tools(10, lambda s: ctx.target if s.id in {"whatweb", "wafw00f"} else None)
        for ln in lines:
            if ln.strip():
                tech.setdefault("scan", ln.strip()[:200])
        ctx.write_lines(self.artifact, [f"{k}: {v}" for k, v in tech.items()])
        ctx.surface.tech_stack = tech
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(tech)}
