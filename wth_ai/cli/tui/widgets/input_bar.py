"""Command input bar."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import Input


class Submitted(Message):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__()


class InputBar(Input):
    BINDINGS = [
        ("escape", "clear_input", "Clear"),
        ("alt+enter", "submit_multiline", "New line"),
    ]

    def on_mount(self) -> None:
        self.placeholder = "› /recon example.com  or  natural language… (Alt+Enter newline)"

    def action_clear_input(self) -> None:
        self.value = ""

    def action_submit_multiline(self) -> None:
        cur = self.cursor_position if hasattr(self, "cursor_position") else len(self.value)
        self.value = self.value[:cur] + "\n" + self.value[cur:]
        if hasattr(self, "cursor_position"):
            self.cursor_position = cur + 1

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return
        self.value = ""
        self.post_message(Submitted(value))
