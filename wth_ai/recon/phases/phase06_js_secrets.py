from __future__ import annotations

from typing import Any

from wth_ai.models import SecretHit
from wth_ai.recon.deep.js_analyzer import JsAnalyzer
from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase06JsSecrets(BasePhase):
    phase, name, artifact = 6, "js_analysis", "js-files.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        urls = ctx.surface.urls or []
        js_files = [u for u in urls if u.endswith(".js") or "/js/" in u or ".bundle." in u]
        ctx.write_lines(self.artifact, js_files)
        ctx.surface.js_files = js_files
        secrets: list[SecretHit] = []

        base = f"https://{ctx.host}"
        analysis = JsAnalyzer().analyze(js_files, base)
        for route in analysis.get("routes", []):
            if route.startswith("http"):
                if route not in ctx.surface.urls:
                    ctx.surface.urls.append(route)
                if "/api/" in route or "/v1/" in route or "/graphql" in route:
                    if route not in ctx.surface.api_endpoints:
                        ctx.surface.api_endpoints.append(route)
        for sec in analysis.get("secrets", []):
            secrets.append(SecretHit(source="js_analyzer", secret_type="possible", snippet=sec[:200]))

        if js_files:
            ctx.write_lines("_js_input.txt", js_files[:50])
            lines = ctx.run_phase_tools(6, lambda s: f"-l {ctx.out_dir}/_js_input.txt" if s.id in {"trufflehog", "gitleaks"} else None)
            for ln in lines:
                if "secret" in ln.lower() or "key" in ln.lower():
                    secrets.append(SecretHit(source="js_scan", secret_type="possible", snippet=ln[:200]))

        ctx.write_lines("js-routes.txt", analysis.get("routes", [])[:100])
        ctx.surface.secrets = secrets
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {
            "phase": self.phase, "name": self.name,
            "js": len(js_files), "secrets": len(secrets),
            "routes": len(analysis.get("routes", [])),
        }
