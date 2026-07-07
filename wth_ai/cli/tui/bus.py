"""UI event bus — decouple orchestrator callbacks from TUI widgets."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class UiEventBus:
    """Publish UI events from orchestrator / slash handlers to TUI panels."""

    _handlers: dict[str, list[Callable[..., None]]] = field(default_factory=dict)
    _app: Any = field(default=None, repr=False)

    def bind_app(self, app: Any) -> None:
        """Marshal cross-thread emits onto the Textual main loop."""
        self._app = app

    def on(self, event: str, handler: Callable[..., None]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Callable[..., None]) -> None:
        if event in self._handlers:
            self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    def emit(self, event: str, **payload: Any) -> None:
        app = self._app
        if app is not None and hasattr(app, "call_from_thread"):
            try:
                if threading.current_thread() is not threading.main_thread():
                    app.call_from_thread(self._emit_sync, event, payload)
                    return
            except Exception:
                pass
        self._emit_sync(event, payload)

    def _emit_sync(self, event: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers.get(event, []):
            try:
                handler(**payload)
            except Exception:
                pass

    def clear(self) -> None:
        self._handlers.clear()


def use_tui(config: object | None = None) -> bool:
    """True only when fullscreen Textual TUI is explicitly requested."""
    import os

    if os.environ.get("WTH_PLAIN", "").strip() in {"1", "true", "yes"}:
        return False
    if os.environ.get("WTH_TUI", "").strip() in {"0", "false", "no"}:
        return False
    if os.environ.get("WTH_TUI", "").strip() in {"1", "true", "yes"}:
        return True
    if config is not None:
        ui = getattr(config, "ui", None)
        if ui is not None and getattr(ui, "mode", "plain") == "tui":
            return True
    return False
