"""Command palette modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from wth_ai.cli.ui.theme import THEME
from wth_ai.platform.registry import get_platform_registry


class CommandPaletteScreen(ModalScreen[str | None]):
    """Fuzzy slash command picker (Ctrl+P)."""

    DEFAULT_CSS = f"""
    CommandPaletteScreen {{
        align: center middle;
    }}
    #palette-box {{
        width: 80;
        height: auto;
        max-height: 20;
        border: solid {THEME['brand']};
        background: {THEME['surface_alt']};
        padding: 1 2;
    }}
  #palette-results {{
        height: auto;
        max-height: 12;
    }}
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-box"):
            yield Static("[bold]Command palette[/] — type to filter")
            yield Input(placeholder="search commands…", id="palette-input")
            yield Static("", id="palette-results")

    def on_mount(self) -> None:
        self.query_one("#palette-input", Input).focus()
        self._refresh("")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            self._refresh(event.value)

    def _refresh(self, query: str) -> None:
        cmds = get_platform_registry().search(query or "", limit=12)
        lines = []
        for c in cmds:
            lines.append(f"{c.name}  [dim]{c.usage}[/]")
        self.query_one("#palette-results", Static).update("\n".join(lines) or "No matches")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "palette-input":
            return
        q = event.value.strip()
        cmds = get_platform_registry().search(q, limit=1)
        if cmds:
            self.dismiss(cmds[0].name)
        elif q.startswith("/"):
            self.dismiss(q)
        else:
            self.dismiss(None)
