"""Plugin runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus

HookHandler = Callable[[dict[str, Any]], None]


@dataclass
class PluginContext:
    config: WTHConfig
    bus: EventBus
    slash_commands: dict[str, Callable[[list[str]], str]] = field(default_factory=dict)
    hooks: dict[str, list[HookHandler]] = field(default_factory=dict)

    def register_slash(self, name: str, handler: Callable[[list[str]], str]) -> None:
        self.slash_commands[name if name.startswith("/") else f"/{name}"] = handler

    def on(self, event_type: str, handler: HookHandler) -> None:
        self.hooks.setdefault(event_type, []).append(handler)

    def emit_hook(self, event_type: str, payload: dict[str, Any]) -> None:
        for h in self.hooks.get(event_type, []):
            try:
                h(payload)
            except Exception:
                pass
