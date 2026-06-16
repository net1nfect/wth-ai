"""Phase 5: parameter inventory."""

from urllib.parse import parse_qsl, urlparse

from . import ReconPhase


class ParameterMiningPhase(ReconPhase):
    """Extract parameter names from supplied URLs."""

    phase, name = 5, "parameter_mining"

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        parameters = {
            key for url in context.get("urls", []) for key, _ in parse_qsl(urlparse(url).query)
        }
        result["observations"] = sorted(parameters)
        return result
