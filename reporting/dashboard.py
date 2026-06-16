"""Real-time dashboard snapshot builder."""

from typing import Any


class Dashboard:
    """Build serializable dashboard state."""

    def snapshot(self, findings: list[dict[str, Any]], agents: list[dict[str, Any]]) -> dict[str, Any]:
        """Return current dashboard metrics."""
        return {
            "finding_count": len(findings),
            "active_agents": sum(item.get("state") == "running" for item in agents),
            "agents": agents,
        }
