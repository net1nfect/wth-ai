"""Phase 9: TLS evidence analysis."""

from . import ReconPhase


class SSLAnalysisPhase(ReconPhase):
    """Analyze supplied certificate metadata."""

    phase, name = 9, "ssl_analysis"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        result["observations"] = context.get("certificates", [])
        return result
