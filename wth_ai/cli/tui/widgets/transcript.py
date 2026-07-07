"""Scrollable transcript panel."""

from __future__ import annotations

from textual.widgets import RichLog


class TranscriptPanel(RichLog):
    """All user commands and responses."""

    def append_user(self, text: str) -> None:
        self.write(f"[bold cyan]›[/] {text}")

    def append_response(self, text: str) -> None:
        if not text:
            return
        self.write(text)

    def append_token(self, token: str) -> None:
        self.write(token, scroll_end=True)

    def append_system(self, text: str) -> None:
        self.write(f"[dim]{text}[/]")
