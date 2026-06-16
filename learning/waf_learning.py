"""Defensive response learning."""

from typing import Any


class WAFLearning:
    """Track defensive-control response rates without bypass guidance."""

    def summarize(self, observations: list[dict[str, Any]]) -> dict[str, Any]:
        """Summarize blocked and allowed validation canaries."""
        blocked = sum(bool(item.get("blocked")) for item in observations)
        return {"observations": len(observations), "blocked": blocked}
