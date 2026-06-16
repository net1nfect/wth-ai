"""Phase 12 — live agent http_probe on high-value graph endpoints."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from wth_ai.recon.phases.base import BasePhase, PhaseContext

if TYPE_CHECKING:
    from wth_ai.terminal.bridge import AgentTerminalBridge


class Phase12LiveAgent(BasePhase):
    phase, name, artifact = 12, "live_agent", "live-probes.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        bridge: AgentTerminalBridge | None = getattr(ctx, "terminal_bridge", None)
        lines: list[str] = []
        probed = 0

        urls: list[str] = []
        for p in ctx.surface.parameters[:20]:
            if p.param.lower() in ("id", "user_id", "url", "redirect", "q", "search"):
                urls.append(p.url if "?" in p.url else f"{p.url}?{p.param}=test")
        urls.extend(ctx.surface.api_endpoints[:10])
        urls = list(dict.fromkeys(urls))[:15]

        if bridge:
            for url in urls:
                if not url.startswith("http"):
                    url = f"https://{ctx.host}{url if url.startswith('/') else '/' + url}"
                result = bridge.http_probe("GET", url, actor="recon", purpose="phase12_live_agent")
                lines.append(f"[{result.transcript_id}] {result.status_code or result.exit_code} {url[:100]}")
                probed += 1
        else:
            for url in urls[:5]:
                lines.append(f"SKIP (no bridge): {url}")

        ctx.write_lines(self.artifact, lines)
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {"phase": self.phase, "name": self.name, "probed": probed}
