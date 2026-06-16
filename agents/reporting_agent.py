"""Reporting agent."""

from core.models import Result

from .base_agent import BaseAgent


class ReportingAgent(BaseAgent):
    """Compile shared knowledge into report-ready data."""

    agent_type = "reporting"

    async def execute(self, task_data: dict) -> Result:
        """Return a shared knowledge snapshot."""
        return Result(True, {"knowledge": await self.knowledge_base.snapshot()})
