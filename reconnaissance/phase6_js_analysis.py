"""Phase 6: JavaScript metadata analysis."""

import re

from . import ReconPhase


class JavaScriptAnalysisPhase(ReconPhase):
    """Extract likely relative API routes from supplied JavaScript text."""

    phase, name = 6, "js_analysis"
    route_pattern = re.compile(r"[\"'](/(?:api|v\d+)/[A-Za-z0-9_./{}-]+)[\"']")

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        routes = {
            match for source in context.get("javascript_sources", []) for match in self.route_pattern.findall(source)
        }
        result["observations"] = sorted(routes)
        return result
