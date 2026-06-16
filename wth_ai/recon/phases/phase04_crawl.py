from __future__ import annotations

import re
from typing import Any

from wth_ai.recon.phases.base import BasePhase, PhaseContext

API_RE = re.compile(r"/api/|/v1/|/v2/|/graphql|/rest/", re.I)


class Phase04Crawl(BasePhase):
    phase, name, artifact = 4, "content_discovery", "urls.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        host = ctx.target
        urls: set[str] = set()
        live = [h.url if h.url.startswith("http") else f"https://{h.url.split()[0]}" for h in ctx.surface.live_hosts[:25]]
        if live:
            ctx.write_lines("_katana_input.txt", live)
            lines = ctx.run_phase_tools(
                4,
                lambda s: (
                    f"-list {ctx.out_dir}/_katana_input.txt -d 3 -jc -kf all -silent"
                    if s.id == "katana"
                    else f"{host} --subs" if s.id == "gau"
                    else None if ctx.fast and s.id in {"gau", "waybackurls"}
                    else f"| waybackurls" if s.id == "waybackurls"
                    else None
                ),
            )
            urls.update(ln.strip() for ln in lines if ln.strip().startswith("http"))

        if not ctx.fast:
            wb = ctx.run_phase_tools(4, lambda s: None)
            urls.update(ln.strip() for ln in wb if ln.strip().startswith("http"))

        merged = ctx.merge_file(self.artifact, list(urls))
        ctx.surface.urls = merged
        ctx.surface.api_endpoints = [u for u in merged if API_RE.search(u)]
        ctx.write_lines("api-endpoints.txt", ctx.surface.api_endpoints)
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(merged), "api": len(ctx.surface.api_endpoints)}
