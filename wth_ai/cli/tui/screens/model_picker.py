"""Native Textual model picker.

The old `/model` path opened a prompt_toolkit application from inside the
running Textual application.  Two event loops cannot own the same terminal, so
the picker appeared to do nothing or corrupted the screen.  This screen stays
inside Textual, fetches models off the UI thread in the caller, and returns the
chosen model id as a plain string.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from wth_ai.cli.tui.widgets.clipboard_input import ClipboardInput


class ModelPickerScreen(ModalScreen[str | None]):
    """Searchable model list. Returns the selected model id or None on Esc."""

    BINDINGS = [
        Binding("escape", "cancel", "Batal"),
        Binding("down", "focus_list", "Daftar", show=False),
    ]

    def __init__(
        self,
        models: list[str],
        *,
        current: str = "",
        provider: str = "",
        title: str | None = None,
    ) -> None:
        super().__init__()
        self._models = sorted(dict.fromkeys(m for m in models if m))
        self._current = current
        self._provider = provider
        self._title = title or "Pilih model"
        self._filtered: list[str] = list(self._models)

    def compose(self) -> ComposeResult:
        with Vertical(id="model-box"):
            yield Static(
                f"[bold]{self._title}[/] [dim]· {self._provider or 'default'} · Esc batal[/]",
                id="model-title",
            )
            yield ClipboardInput(placeholder="cari model…  (↑↓ pilih · Enter pakai)", id="model-search")
            yield OptionList(id="model-list")
            yield Static(
                f"{len(self._models)} model tersedia"
                + (f" · aktif: {self._current}" if self._current else ""),
                id="model-status",
            )

    def on_mount(self) -> None:
        self._refresh("")
        self.query_one("#model-search", ClipboardInput).focus()

    def _refresh(self, query: str) -> None:
        q = query.strip().lower()
        self._filtered = [m for m in self._models if q in m.lower()] if q else list(self._models)
        option_list = self.query_one("#model-list", OptionList)
        option_list.clear_options()
        options = []
        for model in self._filtered:
            marker = "● " if model == self._current else "  "
            options.append(Option(f"{marker}{model}", id=f"model:{model}"))
        option_list.add_options(options)
        status = self.query_one("#model-status", Static)
        if not self._models:
            status.update("[yellow]Tidak ada model dari provider ini.[/]")
        elif not self._filtered:
            status.update(f"Tidak ada hasil untuk [bold]{query}[/]")
        else:
            status.update(
                f"{len(self._filtered)}/{len(self._models)} model · Enter pakai · Esc batal"
            )
        if self._filtered:
            option_list.highlighted = 0

    async def on_input_changed(self, event: ClipboardInput.Changed) -> None:
        self._refresh(event.value)

    def on_input_submitted(self, event: ClipboardInput.Submitted) -> None:
        self._choose_highlighted()

    def _choose_highlighted(self) -> None:
        option_list = self.query_one("#model-list", OptionList)
        index = option_list.highlighted
        if index is not None and 0 <= index < len(self._filtered):
            self.dismiss(self._filtered[index])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        index = event.option_index
        if 0 <= index < len(self._filtered):
            self.dismiss(self._filtered[index])

    def action_focus_list(self) -> None:
        self.query_one("#model-list", OptionList).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)
