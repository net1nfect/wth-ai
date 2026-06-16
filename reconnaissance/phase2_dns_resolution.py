"""Phase 2: DNS evidence normalization."""

from . import ReconPhase


class DNSResolutionPhase(ReconPhase):
    """Normalize supplied DNS records."""

    phase, name = 2, "dns_resolution"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        result["observations"] = context.get("dns_records", [])
        return result
