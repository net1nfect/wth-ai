from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from wth_ai.models import ParamCandidate
from wth_ai.recon.deep.param_ranker import ParamRanker
from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase05Params(BasePhase):
    phase, name, artifact = 5, "parameter_mining", "params.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        urls = ctx.surface.urls or ctx.read_lines("urls.txt")
        params: list[ParamCandidate] = []
        for url in urls[:500]:
            parsed = urlparse(url)
            for key in parse_qs(parsed.query):
                params.append(ParamCandidate(url=url, param=key))

        ranked = ParamRanker().rank(params, limit=100)
        params = [r.candidate for r in ranked]

        ctx.write_lines("_urls_pipe.txt", urls)
        for pattern in ("xss", "ssrf", "idor", "sqli", "redirect", "lfi"):
            lines = ctx.run_phase_tools(5, lambda s, p=pattern: f"patterns/{p}" if s.id == "gf" else None)
            if lines:
                ctx.write_lines(f"{pattern}-candidates.txt", lines)
                for ln in lines[:50]:
                    if "?" in ln:
                        u = ln.split()[0] if " " in ln else ln
                        parsed = urlparse(u)
                        for key in parse_qs(parsed.query):
                            params.append(ParamCandidate(url=u, param=key))

        ranked = ParamRanker().rank(list({(p.url, p.param): p for p in params}.values()), limit=150)
        params = [r.candidate for r in ranked]
        ctx.write_lines(self.artifact, [f"[{r.score}] {r.candidate.url}?{r.candidate.param}" for r in ranked[:200]])
        ctx.surface.parameters = params
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(params), "top_score": ranked[0].score if ranked else 0}
