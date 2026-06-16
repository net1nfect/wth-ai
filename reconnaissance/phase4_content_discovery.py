"""Phase 4: content inventory normalization."""

from . import ReconPhase


class ContentDiscoveryPhase(ReconPhase):
    """Normalize supplied in-scope URLs."""

    phase, name = 4, "content_discovery"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        result["observations"] = sorted(set(context.get("urls", [])))
        return result
