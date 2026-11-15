"""Slash help overlay."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from wth_ai.cli.ui.theme import THEME
from wth_ai.platform.registry import get_platform_registry


class SlashHelpScreen(ModalScreen[None]):
    DEFAULT_CSS = f"""
    SlashHelpScreen {{
        align: center middle;
    }}
    #help-box {{
        width: 90;
        height: 24;
        border: solid {THEME['border']};
        background: {THEME['surface_alt']};
        padding: 1 2;
    }}
    """

    def compose(self) -> ComposeResult:
        text = get_platform_registry().help_text()
        with VerticalScroll(id="help-box"):
            yield Static(text)

    def on_key(self, event) -> None:
        if event.key in {"escape", "q"}:
            self.dismiss(None)
