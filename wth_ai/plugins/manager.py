"""Plugin manager — load hooks and optional slash commands."""

from __future__ import annotations

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus
from wth_ai.plugins.context import PluginContext
from wth_ai.plugins.loader import load_directory_plugins, load_first_party


class PluginManager:
    def __init__(self, config: WTHConfig, bus: EventBus | None = None) -> None:
        from wth_ai.events import get_event_bus
        self.config = config
        self.bus = bus or get_event_bus()
        self.ctx = PluginContext.build(config=config, bus=self.bus)
        self.loaded: list[str] = []
        self._subscribed = False

    def load_all(self) -> list[str]:
        self.loaded = load_first_party(self.ctx) + load_directory_plugins(self.ctx)
        if not self._subscribed:
            self.bus.subscribe(self._on_event)
            self._subscribed = True
        return self.loaded

    def _on_event(self, event) -> None:  # noqa: ANN001
        self.ctx.emit_hook(event.type, {
            "event": event.type,
            **(event.data or {}),
            "job_type": (event.data or {}).get("job_type", ""),
        })

    def slash_handlers(self) -> dict:
        return dict(self.ctx.slash_commands)
