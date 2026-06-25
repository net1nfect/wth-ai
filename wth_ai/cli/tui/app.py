"""Live Textual TUI app (cyberpunk neon) — flowchart TUI layer.

Subscribes to the global event bus for realtime updates and refreshes the
dashboard panels on an interval. Requires a TTY; for headless contexts use
``render_snapshot`` (dashboard.py) instead.
"""

from __future__ import annotations

from typing import Any

from wth_ai.cli.tui.dashboard import DashboardData, render_snapshot


def _build_app() -> Any:  # pragma: no cover - requires textual + TTY
    from rich.console import Group
    from textual.app import App, ComposeResult
    from textual.containers import ScrollableContainer
    from textual.widgets import Footer, Header, Static

    CYBER_CSS = """
    Screen { background: #0b1020; }
    Header { background: #111827; color: #22d3ee; text-style: bold; }
    Footer { background: #111827; color: #a855f7; }
    #board { padding: 1 2; }
    """

    class ReconTUI(App):
        CSS = CYBER_CSS
        TITLE = "WTH-AI · Realtime Recon Platform"
        SUB_TITLE = "cyberpunk console"
        BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

        def __init__(self) -> None:
            super().__init__()
            self.data = DashboardData()
            self._events: list[str] = []
            self._board: Static | None = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            self._board = Static(render_snapshot(self.data), id="board")
            yield ScrollableContainer(self._board)
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(1.5, self.action_refresh)
            try:
                from wth_ai.events import get_event_bus
                get_event_bus().subscribe(self._on_event)
            except Exception:  # noqa: BLE001
                pass

        def _on_event(self, event: Any) -> None:
            self._events.append(getattr(event, "type", "?"))
            self._events = self._events[-30:]

        def action_refresh(self) -> None:
            if self._board is not None:
                snap = render_snapshot(self.data)
                if self._events:
                    from rich.panel import Panel
                    from rich.text import Text
                    stream = Text("\n".join(self._events[-8:]), style="#22d3ee")
                    snap = Group(snap, Panel(stream, title="[b]LIVE EVENTS[/b]",
                                             border_style="#a855f7"))
                self._board.update(snap)

    return ReconTUI()


def run_tui() -> None:  # pragma: no cover - requires a TTY
    app = _build_app()
    app.run()
