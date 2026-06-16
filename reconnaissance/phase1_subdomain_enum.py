"""Phase 1: passive subdomain normalization."""

from . import ReconPhase, target_host


class SubdomainEnumerationPhase(ReconPhase):
    """Normalize explicitly supplied subdomains."""

    phase, name = 1, "subdomain_enumeration"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        host = target_host(context)
        supplied = {str(item).lower() for item in context.get("seed_subdomains", [])}
        result["observations"] = sorted(item for item in supplied | {host} if item == host or item.endswith(f".{host}"))
        return result
