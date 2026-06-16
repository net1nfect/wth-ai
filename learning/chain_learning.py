"""Attack-chain risk modeling."""

from typing import Any


class ChainLearning:
    """Rank supplied risk paths without executing them."""

    def rank(self, chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort paths by supplied risk score."""
        return sorted(chains, key=lambda item: float(item.get("risk_score", 0)), reverse=True)
