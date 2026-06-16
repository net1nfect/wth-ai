"""Reconnaissance agent."""

from typing import Any

from core.models import Result

from .base_agent import BaseAgent


class ReconnaissanceAgent(BaseAgent):
    """Coordinate authorized reconnaissance tasks."""

    agent_type = "reconnaissance"

    async def execute(self, task_data: dict[str, Any]) -> Result:
        """Normalize reconnaissance observations."""
        observations = task_data.get("observations", [])
        await self.update_shared_knowledge("recon_observations", observations)
        return Result(True, {"observations": observations, "count": len(observations)})
