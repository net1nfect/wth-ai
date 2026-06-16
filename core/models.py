"""Shared models and concurrency primitives."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class AgentState(str, Enum):
    """Lifecycle state for an autonomous agent."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(slots=True)
class Message:
    """Inter-agent message."""

    sender: str
    recipient: str
    type: str
    data: dict[str, Any]
    message_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class Result:
    """Normalized task result."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0


class AsyncMessageQueue:
    """Addressable asyncio queues with broadcast support."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Message]] = {}
        self._lock = asyncio.Lock()

    async def register(self, recipient: str) -> None:
        """Register a recipient queue."""
        async with self._lock:
            self._queues.setdefault(recipient, asyncio.Queue())

    async def send(self, message: Message) -> None:
        """Deliver a message to one recipient."""
        await self.register(message.recipient)
        await self._queues[message.recipient].put(message)

    async def broadcast(self, message: Message) -> None:
        """Deliver a message to all recipients except the sender."""
        async with self._lock:
            recipients = tuple(self._queues)
        await asyncio.gather(
            *(
                self.send(
                    Message(
                        sender=message.sender,
                        recipient=recipient,
                        type=message.type,
                        data=message.data,
                    )
                )
                for recipient in recipients
                if recipient != message.sender
            )
        )

    async def receive(self, recipient: str, timeout: float = 300.0) -> Message:
        """Receive the next message for a recipient."""
        await self.register(recipient)
        return await asyncio.wait_for(self._queues[recipient].get(), timeout)


class SharedKnowledgeBase:
    """Concurrency-safe in-memory knowledge store."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def update(self, key: str, value: Any) -> None:
        """Set a knowledge entry."""
        async with self._lock:
            self._data[key] = value

    async def get(self, key: str, default: Any = None) -> Any:
        """Read a knowledge entry."""
        async with self._lock:
            return self._data.get(key, default)

    async def snapshot(self) -> dict[str, Any]:
        """Return a shallow snapshot."""
        async with self._lock:
            return dict(self._data)


@dataclass
class PerformanceTracker:
    """Collect basic task performance metrics."""

    tasks: int = 0
    successes: int = 0
    failures: int = 0
    total_duration_seconds: float = 0.0

    def record(self, result: Result) -> None:
        """Record a completed task."""
        self.tasks += 1
        self.successes += int(result.success)
        self.failures += int(not result.success)
        self.total_duration_seconds += result.duration_seconds

    def report(self) -> dict[str, Any]:
        """Return serializable performance metrics."""
        report = asdict(self)
        report["success_rate"] = self.successes / self.tasks if self.tasks else 0.0
        return report
