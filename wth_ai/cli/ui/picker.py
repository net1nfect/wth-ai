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

try:  # logic below stays importable without prompt_toolkit
    from prompt_toolkit.application.current import get_app
except ImportError:  # pragma: no cover - prompt_toolkit is a declared dependency
    def get_app():  # type: ignore[misc]
        raise RuntimeError("prompt_toolkit tidak terpasang")


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
    handler_for: Callable[[int], Any] | None = None,
) -> list[tuple]:
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
        style = "class:sel" if selected else "class:item"
        handler = handler_for(real) if handler_for is not None else None
        label = f" {marker} {item}"
        rows.append((style, label, handler) if handler is not None else (style, label))
        if describe is not None:
            note = describe(item)
            if note:
                text = f"      {note}"
                rows.append(("class:desc", text, handler) if handler is not None else ("class:desc", text))
    return rows


def wheel_delta(event_data: str) -> int:
    """-1 (up) / +1 (down) / 0 (not a wheel event) for a `Vt100MouseEvent` payload.

    Handles both encodings terminals use:
      SGR : ``\x1b[<64;Cb;CcM`` (64 = up, 65 = down)
      X10 : ``\x1b[M`` + 3 bytes, where the first byte minus 32 is the button code.

    Needed because an INLINE (non-fullscreen) app only receives the wheel on the
    cell under the pointer: the terminal reports the event at the cursor position
    and prompt_toolkit dispatches it to the control that owns that cell. So the
    wheel "did not work" unless the pointer happened to sit on the list.
    """
    if not event_data:
        return 0
    if event_data.startswith("\x1b[<"):
        try:
            button = int(event_data[3:].split(";", 1)[0])
        except (ValueError, IndexError):
            return 0
        return -1 if button == 64 else (1 if button == 65 else 0)
    if event_data.startswith("\x1b[M") and len(event_data) >= 4:
        code = ord(event_data[3]) - 32
        return -1 if code == 64 else (1 if code == 65 else 0)
    return 0


def item_click_handler(state: "ListPickerState", result: dict, index: int):
    """Closure bound to ONE row: select it, or activate it when already selected.

    Attached to each item row's own fragments (prompt_toolkit resolves a click
    through the fragment's handler), so the click does not depend on the
    screen->line coordinate translation. Measured: that translation clamps a click
    that lands outside the rendered content to the last visible line, which is how
    "click the title to cancel" turned into "any click cancels".
    """
    from prompt_toolkit.mouse_events import MouseEventType

    def _handler(mouse_event) -> None:
        if mouse_event.event_type != MouseEventType.MOUSE_UP:
            return None
        if index == state.index:
            result["value"] = state.selected()
            get_app().exit()
        else:
            state.index = index
            get_app().invalidate()
        return None

    return _handler


def build_picker_control(state: "ListPickerState", result: dict, *, per_item: int = 1):
    """Control with wheel + click support, module level so a test can drive it.

    `mouse_handler` is a METHOD on UIControl in this prompt_toolkit version (not a
    constructor argument), and `MouseEvent` carries no `.app`, so the running
    application comes from `get_app()`.
    """
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.mouse_events import MouseEventType

    class PickerControl(FormattedTextControl):
        def mouse_handler(self, mouse_event):  # noqa: ANN001
            app = get_app()
            event_type = mouse_event.event_type

            # Wheel / two-finger touchpad scroll arrives here as SCROLL_* (the
            # terminal's scroll KEYS arrive as key bindings).
            if event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
                state.move(-3 if event_type == MouseEventType.SCROLL_UP else 3)
                app.invalidate()
                return None
            if event_type != MouseEventType.MOUSE_UP:
                return None

            row = mouse_event.position.y
            if row < PICKER_HEADER_ROWS:
                # A click on the title/query row does NOTHING. It used to cancel
                # "like Esc", and that turned out to be dangerous: prompt_toolkit
                # clamps a click outside the rendered content to the last visible
                # line, so in some layouts every click arrived as row 0 -- i.e.
                # every click closed the picker. Cancelling is Esc's job.
                return None

            row_in_list = row - PICKER_HEADER_ROWS
            if row_in_list % per_item:
                return None                      # description row: not a target
            visible, offset = state.window_slice()
            position = row_in_list // per_item
            if position >= len(visible):
                return None                      # footer/empty space
            real = offset + position
            if real == state.index:
                result["value"] = state.selected()
                app.exit()
            else:
                state.index = real
            app.invalidate()
            return None

    return PickerControl


#: title + status, then the filter line: rows before the first item.
PICKER_HEADER_ROWS = 2


def build_picker_bindings(state: "ListPickerState", result: dict) -> Any:
    """Key bindings for the picker, built outside the app so tests drive the SAME
    bindings the REPL uses (a test with an empty KeyBindings() proved nothing about
    arrow keys)."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    bindings = KeyBindings()

    # Registered EAGER so it runs before the default mouse dispatch: a wheel event
    # anywhere in the app scrolls the list, instead of only when the pointer sits
    # on a list row. Returning NotImplemented for anything that is not a wheel keeps
    # clicks and drags on the normal path.
    @bindings.add(Keys.Vt100MouseEvent, eager=True)
    def _wheel_anywhere(event):
        delta = wheel_delta(event.data)
        if not delta:
            return NotImplemented
        state.move(delta * 3)
        event.app.invalidate()
        return None

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
        result["value"] = state.selected()
        event.app.exit()

    @bindings.add("escape")
    @bindings.add("c-c")
    def _cancel(event):
        result["value"] = None
        event.app.exit()

    # Everything else types into the filter (including backspace).
    #
    # CATATAN bug nyata (traceback di terminal operator): cabang `elif on_render ...`
    # di sini mereferensikan parameter yang hanya ada di `pick_from_list`, bukan di
    # fungsi binding ini -- akibat ekstraksi yang memindahkan blok tanpa membawa nama
    # yang dipakainya. Cabang itu memang mati (render dijalankan `invalidate()`), jadi
    # dihapus, bukan disambungkan.
    @bindings.add("<any>")
    def _type(event):
        char = event.data
        if char == "\x7f" or char == "\b":
            state.set_query(state.query[:-1])
        elif char.isprintable():
            state.set_query(state.query + char)
        event.app.invalidate()

    return bindings


def build_picker_app(state: "ListPickerState", result: dict, *, bindings, style, per_item=1,
                     render=None, **kwargs):
    """The picker Application, built here so a test can drive it with a pipe input.

    Extracted after a wheel/touchpad report: `pick_from_list` hid the app, so there
    was no way to feed it real mouse sequences and see whether the handler ran.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.layout import Layout, Window

    control = build_picker_control(state, result, per_item=per_item)(render, focusable=True)
    return Application(
        layout=Layout(Window(control)),
        key_bindings=bindings,
        style=style,
        # Alternate screen ON (full_screen=True): terminals such as VTE
        # (GNOME Terminal / Kali's default) only forward wheel events to the app in
        # the alternate screen -- inline, the wheel scrolls the terminal's own
        # scrollback and the app never sees it. Measured symptom: the wheel worked
        # in tests (events injected directly) but not in the operator's terminal.
        full_screen=True,
        # Wheel/touchpad scrolling and clicking, same as the command menu.
        mouse_support=True,
        **kwargs,
    )


def pick_from_list(
    title: str,
    items: Sequence[str],
    *,
    subtitle: str = "",
    window: int = 12,
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
    # Rows per entry: a label plus (when descriptions are on) a description line.
    per_item = 2 if describe is not None else 1
    result: dict[str, Any] = {"value": None}

    def _render() -> FormattedText:
        rows = FormattedText()
        rows.append(("class:title", f" {title}  "))
        rows.append(("class:dim", f"{state.status()}\n"))
        rows.append(("class:query", " ❯ "))
        rows.append(("class:query", state.query + ("▏" if state.query else "ketik untuk menyaring")))
        rows.append(("class:dim", "\n"))
        for fragment in format_rows(state, describe,
                                    handler_for=lambda real: item_click_handler(state, result, real)):
            style, text = fragment[0], fragment[1]
            handler = fragment[2] if len(fragment) > 2 else None
            rows.append((style, text + "\n", handler) if handler is not None else (style, text + "\n"))
        visible_now, offset_now = state.window_slice()
        hidden_above = offset_now
        hidden_below = max(0, len(state.matches) - offset_now - len(visible_now))
        scroll = ""
        if hidden_above:
            scroll += f"▲ {hidden_above} di atas · "
        if hidden_below:
            scroll += f"▼ {hidden_below} di bawah · "
        rows.append(("class:dim", f" {scroll}enter select · esc back · ↑/↓ pilih · ketik cari"))
        if subtitle:
            rows.append(("class:dim", f" · {subtitle}"))
        return rows

    bindings = build_picker_bindings(state, result)
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

    app = build_picker_app(state, result, bindings=bindings, style=style, per_item=per_item,
                           render=_render)
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return None
    return result["value"]


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
