"""Internal event bus for WTH-AI (Fase 3).

Decouples the core app from integrations: the core publishes events here and the
Integration Manager (and any other subscriber) consumes them. No external deps.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# Canonical event types.
EVENT_TYPES: tuple[str, ...] = (
    "app.started",
    "app.error",
    "provider.tested",
    "provider.failed",
    "job.created",
    "job.started",
    "job.progress",
    "job.log",
    "job.approval_required",
    "job.approved",
    "job.denied",
    "job.stopped",
    "job.completed",
    "job.failed",
    "finding.high_priority",
    "report.ready",
    "integration.error",
    # Fase 5 — recon platform events
    "recon.new_asset",
    "recon.new_endpoint",
    "recon.change_detected",
    "recon.report_ready",
    "recon.completed",
)

# Default severity per event type (info unless noted).
_DEFAULT_SEVERITY: dict[str, str] = {
    "app.error": "error",
    "provider.failed": "error",
    "job.approval_required": "warning",
    "job.denied": "warning",
    "job.stopped": "warning",
    "job.failed": "error",
    "finding.high_priority": "warning",
    "integration.error": "error",
}

# Maps event type -> notification-rule key used in integration config.
EVENT_NOTIFY_KEY: dict[str, str] = {
    "job.created": "job_started",
    "job.started": "job_started",
    "job.approval_required": "approval_needed",
    "finding.high_priority": "high_priority_finding",
    "report.ready": "report_ready",
    "job.failed": "job_failed",
    "job.completed": "job_completed",
    "provider.failed": "provider_error",
    "integration.error": "integration_error",
    "recon.change_detected": "high_priority_finding",
    "recon.report_ready": "report_ready",
}


@dataclass
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    severity: str | None = None
    correlation_id: str | None = None
    job_id: str | None = None
    id: str = field(default_factory=lambda: f"EV-{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if self.severity is None:
            self.severity = _DEFAULT_SEVERITY.get(self.type, "info")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp,
            "source": self.source,
            "severity": self.severity,
            "data": self.data,
            "correlation_id": self.correlation_id,
            "job_id": self.job_id,
        }


def make_event(event_type: str, **kwargs: Any) -> Event:
    """Create an event, validating the type."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {event_type}")
    return Event(type=event_type, **kwargs)


Subscriber = Callable[[Event], Any]


class EventBus:
    """Minimal synchronous pub/sub with optional type filtering + history."""

    def __init__(self, *, history_size: int = 200) -> None:
        # (subscriber, allowed_types or None)
        self._subs: list[tuple[Subscriber, frozenset[str] | None]] = []
        self.history: deque[Event] = deque(maxlen=history_size)

    def subscribe(self, handler: Subscriber, types: list[str] | None = None) -> None:
        allowed = frozenset(types) if types else None
        self._subs.append((handler, allowed))

    def unsubscribe(self, handler: Subscriber) -> None:
        self._subs = [(h, t) for (h, t) in self._subs if h is not handler]

    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: Event) -> list[Any]:
        """Deliver to matching subscribers. A failing subscriber never breaks others."""
        self.history.append(event)
        results: list[Any] = []
        for handler, allowed in self._subs:
            if allowed is not None and event.type not in allowed:
                continue
            try:
                results.append(handler(event))
            except Exception as exc:  # noqa: BLE001 - isolation is the whole point
                results.append({"error": str(exc)})
        return results

    def emit(self, event_type: str, **kwargs: Any) -> Event:
        """Convenience: build + publish in one call."""
        event = make_event(event_type, **kwargs)
        self.publish(event)
        return event


_GLOBAL_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    """Process-wide default bus so the core can publish without wiring."""
    global _GLOBAL_BUS
    if _GLOBAL_BUS is None:
        _GLOBAL_BUS = EventBus()
    return _GLOBAL_BUS


def reset_event_bus() -> None:
    """Testing helper: drop the global bus."""
    global _GLOBAL_BUS
    _GLOBAL_BUS = None
