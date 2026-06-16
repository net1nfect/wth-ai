"""Wire the event bus to the integration manager (core stays decoupled)."""

from __future__ import annotations

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus, get_event_bus
from wth_ai.integrations.manager import IntegrationManager


def build_integration_runtime(
    config: WTHConfig,
    *,
    bus: EventBus | None = None,
    dry_run: bool = False,
) -> tuple[EventBus, IntegrationManager]:
    """Create (or reuse) a bus and attach an IntegrationManager to it."""
    bus = bus or get_event_bus()
    manager = IntegrationManager(config, bus, dry_run=dry_run)
    return bus, manager
