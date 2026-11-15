"""Multi-line command input with real terminal clipboard support."""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from wth_ai.attachments import (
    paths_from_paste,
    read_clipboard_image,
    read_clipboard_text,
    save_clipboard_image,
    write_clipboard_text,
)


class Submitted(Message):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__()


class AttachmentRequested(Message):
    """A pasted path/screenshot should be attached instead of inserted as text."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = paths
        super().__init__()


class InputBar(TextArea):
    """TextArea-backed prompt.

    Textual's `Input` is single-line and its paste handler truncates at the first
    newline.  TextArea preserves the entire paste and gives us a real cursor for
    multi-line prompts.  Enter submits; Shift+Enter / Alt+Enter inserts a newline.
    """

    BINDINGS = [
        Binding("enter", "submit", "Kirim", show=False),
        Binding("shift+enter", "newline", "Baris baru", show=False),
        Binding("alt+enter", "newline", "Baris baru", show=False),
        Binding("ctrl+j", "newline", "Baris baru", show=False),
        Binding("escape", "clear_input", "Bersihkan", show=False),
        Binding("ctrl+v", "paste_clipboard", "Tempel", show=False),
        Binding("ctrl+c", "copy_clipboard", "Salin", show=False),
    ]

    def on_mount(self) -> None:
        self.placeholder = (
            "› /recon example.com  ·  tempel path file/foto untuk lampiran  ·  "
            "Shift+Enter baris baru"
        )
        self.show_line_numbers = False

    # ------------------------------------------------------------------ #
    # value compatibility: the rest of the app used Input.value
    # ------------------------------------------------------------------ #
    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.load_text(text or "")

    def action_clear_input(self) -> None:
        self.load_text("")

    def action_submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self.load_text("")
        self.post_message(Submitted(text))

    def action_newline(self) -> None:
        self.insert("\n")

    async def _on_key(self, event: events.Key) -> None:
        # TextArea consumes Enter inside `_on_key` before bindings run. Intercept
        # the submit/newline keys here so Enter sends and Shift+Enter stays a
        # deliberate line break.
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
            return
        if event.key in {"shift+enter", "alt+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            self.action_newline()
            return
        await super()._on_key(event)

    # ------------------------------------------------------------------ #
    # system clipboard
    # ------------------------------------------------------------------ #
    def action_paste_clipboard(self) -> None:
        text = read_clipboard_text()
        if text:
            paths = paths_from_paste(text)
            if paths:
                self.post_message(AttachmentRequested([str(p) for p in paths]))
                return
            self.insert(text)
            return

        image = read_clipboard_image()
        if image:
            data, mime = image
            try:
                path = save_clipboard_image(data, mime=mime)
            except OSError:
                return
            self.post_message(AttachmentRequested([str(path)]))
            return

        # Last resort: Textual's internal clipboard (used by tests and some
        # terminal emulators that do not expose a system selection).
        self.action_paste()

    def action_copy_clipboard(self) -> None:
        text = self.selected_text or self.text
        if not text:
            # Ctrl+C with no selection keeps its chat meaning: interrupt the
            # running turn instead of copying an empty buffer.
            if getattr(self.app, "_busy", False) and hasattr(self.app, "action_interrupt_turn"):
                self.app.action_interrupt_turn()
            return
        write_clipboard_text(text)
        try:
            # Keep Textual's own clipboard in sync for in-app tests.
            self.app.clipboard = text
        except Exception:
            pass

    async def _on_paste(self, event: events.Paste) -> None:
        paths = paths_from_paste(event.text)
        if paths:
            self.post_message(AttachmentRequested([str(p) for p in paths]))
            event.stop()
            return
        await super()._on_paste(event)
