"""Phase 3: HTTP metadata analysis."""

from . import ReconPhase


class HTTPProbingPhase(ReconPhase):
    """Analyze supplied HTTP metadata without sending requests."""

    phase, name = 3, "http_probing"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        result["observations"] = context.get("http_metadata", [])
        return result
