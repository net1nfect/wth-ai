from __future__ import annotations

from typing import Any

from wth_ai.recon.deep.auth_surface import AuthSurfaceDetector
from wth_ai.recon.deep.graphql_probe import GraphqlProbe
from wth_ai.recon.deep.openapi_discovery import OpenApiDiscovery
from wth_ai.recon.phases.base import BasePhase, PhaseContext


class Phase11ApiSchema(BasePhase):
    phase, name, artifact = 11, "api_schema", "api-schema.txt"

    async def run(self, ctx: PhaseContext) -> dict[str, Any]:
        base = f"https://{ctx.host}"
        openapi = OpenApiDiscovery().probe(base)
        graphql = GraphqlProbe().probe(base)
        urls = ctx.surface.urls or []
        auth = AuthSurfaceDetector().summary(urls)

        lines: list[str] = []
        for spec in openapi.get("specs", []):
            lines.append(f"OPENAPI: {spec['url']} ({spec['type']})")
        for ep in openapi.get("endpoints", [])[:30]:
            lines.append(f"API: {ep}")
            if ep not in ctx.surface.api_endpoints:
                ctx.surface.api_endpoints.append(ep.split(" ", 1)[-1] if " " in ep else ep)
        for g in graphql.get("graphql", []):
            lines.append(f"GRAPHQL: {g['url']} introspection={g.get('introspection')}")
            ctx.surface.api_endpoints.append(g["url"])
        for flow in auth.get("flows", []):
            lines.append(f"AUTH_{flow['flow_type'].upper()}: {flow['url']}")

        ctx.write_lines(self.artifact, lines)
        ctx.surface.phase_artifacts[self.name] = str(ctx.out_dir / self.artifact)
        return {
            "phase": self.phase,
            "name": self.name,
            "openapi": len(openapi.get("specs", [])),
            "graphql": len(graphql.get("graphql", [])),
            "auth_flows": auth.get("count", 0),
        }
