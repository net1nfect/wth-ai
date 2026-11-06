"""Plugin runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from wth_ai.config import WTHConfig
from wth_ai.events import EventBus
from wth_ai.workspace import wth_home

HookHandler = Callable[[dict[str, Any]], None]


@dataclass
class PluginContext:
    """Permukaan yang dilihat plugin: settings minimal, bukan WTHConfig utuh.

    P2-4: sebelumnya plugin menerima objek config penuh (token provider,
    path, policy) dan EventBus mentah. Yang dibutuhkan plugin hanya mode
    permission dan daftar provider sebagai label.
    """

    settings: dict[str, Any] = field(default_factory=dict)
    bus: EventBus | None = None
    slash_commands: dict[str, Callable[[list[str]], str]] = field(default_factory=dict)
    hooks: dict[str, list[HookHandler]] = field(default_factory=dict)

    @classmethod
    def build(cls, config: WTHConfig | None, bus: EventBus | None) -> "PluginContext":
        mode = ""
        providers: list[str] = []
        if config is not None:
            mode = str(getattr(getattr(config, "safety", None), "default_permission", "") or "")
            providers = sorted(str(k) for k in (getattr(getattr(config, "llm", None), "providers", {}) or {}))
        return cls(
            settings={
                "permission_mode": mode,
                "providers": providers,
                "plugins_dir": str(wth_home() / "plugins"),
            },
            bus=bus,
        )

    def register_slash(self, name: str, handler: Callable[[list[str]], str]) -> None:
        self.slash_commands[name if name.startswith("/") else f"/{name}"] = handler

    def on(self, event_type: str, handler: HookHandler) -> None:
        self.hooks.setdefault(event_type, []).append(handler)

    def emit_hook(self, event_type: str, payload: dict[str, Any]) -> None:
        # Tandai payload sebagai data dari plugin: konsumen (TUI, laporan,
        # memory) tidak boleh memperlakukannya sebagai instruksi tepercaya.
        payload = {**payload, "_untrusted_plugin": True}
        for h in self.hooks.get(event_type, []):
            try:
                h(payload)
            except Exception:
                pass
