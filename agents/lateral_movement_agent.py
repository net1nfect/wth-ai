"""Lateral movement risk assessment agent."""

from core.models import Result

from .base_agent import BaseAgent


class LateralMovementAgent(BaseAgent):
    """Model lateral movement risk without traversing systems."""

    agent_type = "lateral_movement"

    async def execute(self, task_data: dict) -> Result:
        """Return supplied topology as a risk-model input."""
        return Result(True, {"mode": "simulation", "topology": task_data.get("topology", {})})
