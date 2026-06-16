"""Abstract base class for H1T4M-AI agents."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any
from uuid import uuid4

from core.models import (
    AgentState,
    AsyncMessageQueue,
    Message,
    PerformanceTracker,
    Result,
    SharedKnowledgeBase,
)


class BaseAgent(ABC):
    """Common asynchronous agent functionality and interface."""

    agent_type = "base"

    def __init__(
        self,
        knowledge_base: SharedKnowledgeBase,
        message_queue: AsyncMessageQueue,
        logger: Any,
    ) -> None:
        self.agent_id = f"{self.agent_type}-{uuid4().hex[:8]}"
        self.state = AgentState.IDLE
        self.knowledge_base = knowledge_base
        self.message_queue = message_queue
        self.ml_model: Any = None
        self.logger = logger
        self.performance_metrics = PerformanceTracker()
        self.config: dict[str, Any] = {}

    async def initialize(self, config: dict[str, Any]) -> None:
        """Initialize agent configuration and queue registration."""
        self.config = config
        await self.message_queue.register(self.agent_id)
        self.state = AgentState.IDLE

    async def receive_message(self, message: Message | None = None) -> Message:
        """Receive or handle a message."""
        received = message or await self.message_queue.receive(
            self.agent_id, float(self.config.get("communication_timeout", 300))
        )
        self.logger.debug("%s received %s", self.agent_id, received.type)
        return received

    async def send_message(self, recipient: str, data: dict[str, Any]) -> None:
        """Send a message with exponential-backoff retries."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                await self.message_queue.send(Message(self.agent_id, recipient, "agent_update", data))
                return
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(2**attempt)
        raise RuntimeError(f"Unable to send message: {last_error}")

    async def broadcast_message(self, data: dict[str, Any]) -> None:
        """Broadcast an update to all agents."""
        await self.message_queue.broadcast(Message(self.agent_id, "*", "broadcast", data))

    async def request_coordination(self, action: str, params: dict[str, Any]) -> Result:
        """Request coordinator action."""
        await self.message_queue.send(
            Message(self.agent_id, "coordinator", "coordination_request", {"action": action, **params})
        )
        return Result(True, {"queued": True, "action": action})

    async def update_shared_knowledge(self, key: str, value: Any) -> None:
        """Update the shared knowledge base."""
        await self.knowledge_base.update(key, value)

    async def learn_from_feedback(self, feedback: dict[str, Any]) -> None:
        """Record feedback for the learning agent."""
        await self.update_shared_knowledge(f"feedback:{self.agent_id}", feedback)

    def generate_performance_report(self) -> dict[str, Any]:
        """Return performance metrics."""
        return {"agent_id": self.agent_id, "agent_type": self.agent_type, **self.performance_metrics.report()}

    async def run(self, task_data: dict[str, Any]) -> Result:
        """Execute a task while maintaining state and metrics."""
        self.state = AgentState.RUNNING
        started = time.monotonic()
        try:
            result = await self.execute(task_data)
        except asyncio.CancelledError:
            self.state = AgentState.STOPPED
            raise
        except Exception as exc:
            self.logger.exception("%s failed", self.agent_id)
            self.state = AgentState.ERROR
            result = Result(False, error=str(exc))
        result.duration_seconds = time.monotonic() - started
        self.performance_metrics.record(result)
        if self.state != AgentState.ERROR:
            self.state = AgentState.IDLE
        return result

    @abstractmethod
    async def execute(self, task_data: dict[str, Any]) -> Result:
        """Execute an agent-specific task."""
