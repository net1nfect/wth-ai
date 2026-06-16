"""Phase 8: cloud indicator classification."""

from . import ReconPhase


class CloudEnumerationPhase(ReconPhase):
    """Classify supplied cloud asset metadata."""

    phase, name = 8, "cloud_enumeration"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        result["observations"] = context.get("cloud_assets", [])
        return result
