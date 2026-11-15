"""Single-line Textual input with the real terminal clipboard.

Textual's `Input` uses its internal clipboard for Ctrl+V/Ctrl+C. In a terminal
that is often empty, so a value copied from the browser or another pane cannot
be pasted into the model search / palette. This subclass keeps the single-line
semantics while using the same system clipboard helper as `InputBar`.
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.widgets import Input

from wth_ai.attachments import read_clipboard_text, write_clipboard_text


class ClipboardInput(Input):
    BINDINGS = [
        Binding("ctrl+v", "paste_clipboard", "Tempel", show=False),
        Binding("ctrl+c", "copy_clipboard", "Salin", show=False),
    ]

    def action_paste_clipboard(self) -> None:
        text = read_clipboard_text()
        if text:
            self.insert_text_at_cursor(" ".join(text.splitlines()))
            return
        self.action_paste()

    def action_copy_clipboard(self) -> None:
        text = self.selected_text or self.value
        if text:
            write_clipboard_text(text)
            try:
                self.app.clipboard = text
            except Exception:
                pass

    def _on_paste(self, event: events.Paste) -> None:
        if event.text:
            line = " ".join(event.text.splitlines())
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(line)
            else:
                self.replace(line, *selection)
        event.stop()
