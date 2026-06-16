from __future__ import annotations

from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase09Fingerprint(BasePhase):
    phase, name, artifact = 9, "fingerprinting", "nuclei.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        live = ctx.read_lines("live-hosts.txt")
        if not live:
            return {"phase": self.phase, "name": self.name, "count": 0}
        bare = [ln.split()[0] for ln in live]
        ctx.write_lines("_nuclei_input.txt", bare)
        lines = ctx.run_phase_tools(
            9,
            lambda s: f"-l {ctx.out_dir}/_nuclei_input.txt -severity critical,high,medium -silent" if s.id == "nuclei" else None,
        )
        merged = ctx.merge_file(self.artifact, lines)
        ctx.surface.nuclei = merged
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(merged)}
