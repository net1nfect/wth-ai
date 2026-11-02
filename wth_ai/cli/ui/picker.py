"""Interactive list picker: type to filter, arrows to move, Enter to choose.

Built for the 268-model catalog case: a numbered list forces the operator to read
a wall of text and remember a number, so choosing a model now opens a real
picker.

The selection LOGIC lives in `ListPickerState` -- a plain, dependency-free object
-- and the terminal layer in `pick_from_list()`. That split is deliberate: a TUI
that can only be exercised by hand is a TUI nobody tests, and this module's bugs
are exactly the kind (off-by-one window, filter that forgets to reset the cursor,
selection that does not match what is highlighted) that a unit test catches for
free.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence


def interactive_available() -> bool:
    """True when a real terminal can host an interactive picker.

    Piped input, CI and `--plain` runs must degrade to text instead of blocking
    forever on a picker nobody can see.
    """
    import os

    if os.environ.get("WTH_PLAIN", "").strip() in {"1", "true", "yes"}:
        return False
    if os.environ.get("WTH_NO_PICKER", "").strip() in {"1", "true", "yes"}:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - odd streams must not decide "interactive"
        return False


@dataclass
class ListPickerState:
    """Filter + cursor state for a list picker. No I/O, no terminal."""

    items: list[str]
    query: str = ""
    index: int = 0
    window: int = 12
    offset: int = field(default=0, init=False)

    # ------------------------------------------------------------------ #
    @property
    def matches(self) -> list[str]:
        needle = self.query.strip().lower()
        if not needle:
            return list(self.items)
        return [item for item in self.items if needle in item.lower()]

    def set_query(self, query: str) -> None:
        """Replacing the filter re-anchors the cursor -- otherwise the highlight
        survives a filter change and points at a different item than before."""
        self.query = query
        self.index = 0

    def move(self, delta: int) -> None:
        matches = self.matches
        if not matches:
            self.index = 0
            return
        # Clamp, do not wrap: wrapping from the last row to the first makes it easy
        # to select the wrong model by holding an arrow key one press too long.
        self.index = max(0, min(len(matches) - 1, self.index + delta))

    def selected(self) -> str | None:
        matches = self.matches
        if not matches or self.index >= len(matches):
            return None
        return matches[self.index]

    def window_slice(self) -> tuple[list[str], int]:
        """The visible slice and its offset, with the selection kept in view."""
        matches = self.matches
        if not matches:
            self.offset = 0
            return [], 0
        size = max(1, self.window)
        if self.index < self.offset:
            self.offset = self.index
        elif self.index >= self.offset + size:
            self.offset = self.index - size + 1
        self.offset = max(0, min(self.offset, max(0, len(matches) - size)))
        return matches[self.offset:self.offset + size], self.offset

    def status(self) -> str:
        matches = self.matches
        if not matches:
            return f"0 dari {len(self.items)} cocok"
        return f"{self.index + 1}/{len(matches)} cocok · {len(self.items)} total"


def describe_model(model: str, *, current: str = "", tested: dict[str, bool] | None = None) -> str:
    """One honest line about a model id.

    Only facts the id itself carries plus what we actually observed: which model is
    in use, which passed/failed a probe THIS session, and the vendor/suffix parsed
    from the id. No speed or quality adjectives -- we do not know them, and a picker
    that invents "fast and affordable" is a picker that lies.
    """
    bits: list[str] = []
    if current and model == current:
        bits.append("sedang dipakai")
    tested = tested or {}
    if model in tested:
        bits.append("lolos uji" if tested[model] else "gagal uji di sesi ini")
    vendor = model.split("/")[0] if "/" in model else ""
    if vendor:
        bits.append(f"vendor {vendor}")
    for suffix in (":free", ":batch", ":extended"):
        if model.endswith(suffix):
            bits.append(suffix.lstrip(":"))
            break
    return " · ".join(bits)


def format_rows(
    state: ListPickerState,
    describe: Callable[[str], str] | None = None,
) -> list[tuple[str, str]]:
    """Rows for the picker body. Pure, so the highlight/description logic is testable.

    Two lines per entry, like the model picker operators already know: the name on
    a highlighted row, and a dim description under it.
    """
    rows: list[tuple[str, str]] = []
    visible, offset = state.window_slice()
    if not visible:
        return [("class:dim", "  (tidak ada yang cocok)")]
    for i, item in enumerate(visible):
        real = offset + i
        selected = real == state.index
        marker = "▶" if selected else " "
        rows.append(("class:sel" if selected else "class:item", f" {marker} {item}"))
        if describe is not None:
            note = describe(item)
            if note:
                rows.append(("class:desc", f"      {note}"))
    return rows


def pick_from_list(
    title: str,
    items: Sequence[str],
    *,
    subtitle: str = "",
    window: int = 12,
    on_render: Callable[[ListPickerState], Any] | None = None,
    describe: Callable[[str], str] | None = None,
) -> str | None:
    """Run the picker. Returns the chosen item, or None when cancelled/unavailable."""
    if not items or not interactive_available():
        return None

    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    from wth_ai.cli.ui.theme import THEME

    state = ListPickerState(items=list(items), window=window)

    def _render() -> FormattedText:
        rows = FormattedText()
        rows.append(("class:title", f" {title}  "))
        rows.append(("class:dim", f"{state.status()}\n"))
        rows.append(("class:query", " ❯ "))
        rows.append(("class:query", state.query + ("▏" if state.query else "ketik untuk menyaring")))
        rows.append(("class:dim", "\n"))
        for style, text in format_rows(state, describe):
            rows.append((style, text + "\n"))
        rows.append(("class:dim", " enter select · esc back · ↑/↓ pilih · ketik cari"))
        if subtitle:
            rows.append(("class:dim", f" · {subtitle}"))
        return rows

    bindings = KeyBindings()

    @bindings.add("up")
    @bindings.add("c-p")
    def _up(event):
        state.move(-1)
        event.app.invalidate()

    @bindings.add("down")
    @bindings.add("c-n")
    def _down(event):
        state.move(1)
        event.app.invalidate()

    @bindings.add("pageup")
    def _page_up(event):
        state.move(-window)
        event.app.invalidate()

    @bindings.add("pagedown")
    def _page_down(event):
        state.move(window)
        event.app.invalidate()

    @bindings.add("enter")
    def _accept(event):
        event.app.exit(result=state.selected())

    @bindings.add("escape")
    @bindings.add("c-c")
    def _cancel(event):
        event.app.exit(result=None)

    # Everything else types into the filter (including backspace).
    @bindings.add("<any>")
    def _type(event):
        char = event.data
        if char == "\x7f" or char == "\b":
            state.set_query(state.query[:-1])
        elif char.isprintable():
            state.set_query(state.query + char)
        elif on_render is not None:
            on_render(state)
        event.app.invalidate()

    style = Style.from_dict({
        "title": f"bold {THEME['brand']}",
        "dim": THEME["muted"],
        "query": THEME["text"],
        # Selected row gets a background, like the pickers operators already use:
        # a bold colour alone is easy to lose when the list is long.
        "sel": f"bold {THEME['accent']} bg:{THEME['surface_alt']}",
        "item": THEME["text"],
        "desc": THEME["muted"],
    })

    app: Application = Application(
        layout=Layout(Window(FormattedTextControl(_render))),
        key_bindings=bindings,
        style=style,
        full_screen=False,
    )
    try:
        return app.run()
    except (EOFError, KeyboardInterrupt):
        return None


def show_panel(title: str, lines: Iterable[str]) -> None:
    """Print a bordered panel (used by `?` help and the agents view).

    A printed panel rather than a modal overlay on purpose: it works from inside a
    key binding via `run_in_terminal`, and it stays in the scrollback where the
    operator can still read it after continuing.
    """
    from io import StringIO

    from rich.console import Console
    from rich.panel import Panel

    from wth_ai.cli.ui.theme import THEME

    body = "\n".join(lines)
    buf = StringIO()
    Console(file=buf, width=max(60, min(120, _terminal_width())), highlight=False,
            force_terminal=True, emoji=False).print(
        Panel(body, title=title, border_style=THEME["border"], title_align="left")
    )
    sys.stdout.write(buf.getvalue())


def _terminal_width() -> int:
    import shutil

    return shutil.get_terminal_size((100, 24)).columns
