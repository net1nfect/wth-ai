"""Phase 10: technology fingerprint normalization."""

from . import ReconPhase


class FingerprintingPhase(ReconPhase):
    """Normalize supplied technology fingerprints."""

    phase, name = 10, "fingerprinting"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        result["observations"] = sorted(set(context.get("technologies", [])))
        return result
