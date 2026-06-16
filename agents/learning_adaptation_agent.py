"""Learning and adaptation agent."""

from core.models import Result

from .base_agent import BaseAgent


class LearningAdaptationAgent(BaseAgent):
    """Learn prioritization signals from engagement feedback."""

    agent_type = "learning_adaptation"

    async def execute(self, task_data: dict) -> Result:
        """Persist normalized feedback in shared knowledge."""
        feedback = task_data.get("feedback", [])
        await self.update_shared_knowledge("learning_feedback", feedback)
        return Result(True, {"samples": len(feedback)})
