from __future__ import annotations

import os
from typing import Any

import requests

from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase01Subdomain(BasePhase):
    phase, name, artifact = 1, "subdomain_enumeration", "subdomains.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        host = ctx.target
        found: set[str] = {host}
        chaos_key = os.environ.get("CHAOS_API_KEY", "")
        if chaos_key:
            try:
                r = requests.get(
                    f"https://dns.projectdiscovery.io/dns/{host}/subdomains",
                    headers={"Authorization": chaos_key},
                    timeout=30,
                )
                if r.ok and isinstance(r.json(), list):
                    found.update(str(s).lower() for s in r.json())
            except requests.RequestException as exc:
                ctx.warnings.append(f"chaos: {exc}")

        lines = ctx.run_phase_tools(1, lambda s: f"-d {host} -silent" if s.id == "subfinder" else f"--subs-only {host}" if s.id == "assetfinder" else None)
        found.update(ln.strip().lower() for ln in lines if ln.strip() and "." in ln)
        merged = ctx.merge_file(self.artifact, list(found))
        ctx.surface.subdomains = merged
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "count": len(merged)}
