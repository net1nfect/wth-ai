"""Agent and queue tests."""

import unittest

from agents import AGENT_CLASSES
from core.logger import get_logger
from core.models import AsyncMessageQueue, Message, SharedKnowledgeBase


class AgentTests(unittest.IsolatedAsyncioTestCase):
    """Validate all agents and communication."""

    async def test_all_nine_agents_initialize_and_execute(self) -> None:
        queue = AsyncMessageQueue()
        knowledge = SharedKnowledgeBase()
        for name, agent_type in AGENT_CLASSES.items():
            agent = agent_type(knowledge, queue, get_logger(f"test.{name}"))
            await agent.initialize({"communication_timeout": 1})
            result = await agent.run({})
            self.assertTrue(result.success, name)

    async def test_message_queue(self) -> None:
        queue = AsyncMessageQueue()
        await queue.send(Message("sender", "recipient", "test", {"ok": True}))
        message = await queue.receive("recipient", 1)
        self.assertEqual(message.data, {"ok": True})
