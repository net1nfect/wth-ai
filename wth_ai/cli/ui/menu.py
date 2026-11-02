"""Hierarchical, scrollable, mouse-clickable menu for the REPL.

What the operator asked for, and what this implements:

* typing `/` and pressing Enter opens a menu you can WALK with the keyboard;
* the list scrolls (arrows, page keys, home/end, and the mouse WHEEL), and rows
  are CLICKABLE with the cursor;
* Enter on a command with subcommands opens ITS menu (e.g. `/config` -> show/set);
* Esc goes BACK one level, and closes the menu at the root.

Navigation lives in `MenuState`, a plain object: depth, cursor, scroll offset and
the row->item mapping for clicks are exactly the things that break in a TUI and
exactly the things a unit test can pin down without a terminal. The terminal
layer (`run_menu`) only renders and forwards events.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

try:  # logic above stays importable without prompt_toolkit
    from prompt_toolkit.layout.controls import FormattedTextControl
except ImportError:  # pragma: no cover - prompt_toolkit is a declared dependency
    FormattedTextControl = None  # type: ignore[assignment]

    def get_app():  # type: ignore[misc]
        raise RuntimeError("prompt_toolkit tidak terpasang")

#: Rows the control spends on things that are not items (title + status line).
#: The mouse handler needs this to turn a click's y coordinate into an item index.
HEADER_ROWS = 2


def interactive_available() -> bool:
    """Same rule as the picker: never open a menu nobody can see or drive."""
    import os

    if os.environ.get("WTH_PLAIN", "").strip() in {"1", "true", "yes"}:
        return False
    if os.environ.get("WTH_NO_MENU", "").strip() in {"1", "true", "yes"}:
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


if FormattedTextControl is not None:

    class MenuControl(FormattedTextControl):  # type: ignore[misc,valid-type]
        """Menu control with click handling.

        Module level (not nested inside `run_menu`) so a test can drive a real click
        against it. `mouse_handler` is a METHOD on UIControl -- passing it as a
        constructor kwarg raises in this prompt_toolkit version.
        """

        def __init__(self, text, state: "MenuState", result: dict, *, enabled: bool = True) -> None:
            super().__init__(text, focusable=True)
            self._state = state
            self._result = result
            self._mouse_enabled = enabled

        def mouse_handler(self, mouse_event):  # noqa: ANN001 - prompt_toolkit type
            from prompt_toolkit.mouse_events import MouseEventType

            state = self._state
            # `MouseEvent` carries position/type/button/modifiers only -- there is
            # no `.app` attribute, so reaching for one raises AttributeError on
            # every click. The running application comes from `get_app()`.
            app = get_app()
            event_type = mouse_event.event_type

            # Wheel: scroll a few rows, like any list in a terminal. The wheel
            # arrives here (SCROLL_UP/SCROLL_DOWN); the terminal's scroll KEYS
            # arrive as key bindings -- both are wired.
            if event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
                state.move(-3 if event_type == MouseEventType.SCROLL_UP else 3)
                app.invalidate()
                return None

            if event_type != MouseEventType.MOUSE_UP:
                return None

            row = mouse_event.position.y
            if row < HEADER_ROWS:
                # Not "click the breadcrumb to go back": prompt_toolkit clamps
                # clicks that land outside the rendered content to the last visible
                # line, so a title-row rule can fire on ANY click (measured: every
                # click arrived as row 0). Back is Esc/backspace/left-arrow.
                return None

            index = state.index_at_row(row)
            if index is None:
                return None
            if index == state.index:
                # A click on the already-highlighted row activates it.
                item = state.enter()
                if item is not None:
                    self._result["value"] = item.action
                    app.exit()
            else:
                state.index = index
            app.invalidate()
            return None

else:  # pragma: no cover
    MenuControl = None  # type: ignore[assignment]


@dataclass
class MenuItem:
    """One row. `children` makes it a submenu; otherwise `action` is submitted."""

    label: str
    description: str = ""
    action: str = ""
    children: list["MenuItem"] = field(default_factory=list)
    #: True when the usage entry needs an argument (`use N`, `graph query`,
    #: `config set key=val`). Measured: offering those as if they were complete
    #: commands made the menu submit half a command -- one of them persisted a
    #: model named after the verb. The REPL pre-fills the prompt instead.
    needs_args: bool = False

    @property
    def is_submenu(self) -> bool:
        return bool(self.children)


@dataclass
class _Level:
    title: str
    items: list[MenuItem]


class MenuState:
    """Stack of levels + cursor + scroll window. Deliberately terminal-free."""

    def __init__(self, items: Sequence[MenuItem], *, title: str = "Command", window: int = 12) -> None:
        self.window = max(3, window)
        self.stack: list[_Level] = [_Level(title=title, items=list(items))]
        self.index = 0
        self.offset = 0

    # ------------------------------------------------------------------ #
    @property
    def title(self) -> str:
        return self.stack[-1].title

    @property
    def items(self) -> list[MenuItem]:
        return self.stack[-1].items

    @property
    def depth(self) -> int:
        return len(self.stack)

    def breadcrumb(self) -> str:
        return " › ".join(level.title for level in self.stack)

    def visible(self) -> tuple[list[MenuItem], int]:
        """Visible slice + offset, keeping the cursor on screen."""
        count = len(self.items)
        if not count:
            self.offset = 0
            return [], 0
        if self.index < self.offset:
            self.offset = self.index
        elif self.index >= self.offset + self.window:
            self.offset = self.index - self.window + 1
        self.offset = max(0, min(self.offset, max(0, count - self.window)))
        return self.items[self.offset:self.offset + self.window], self.offset

    def scroll_hint(self) -> str:
        """`▲ 3 di atas · ▼ 5 di bawah` — without it a long list looks complete.

        Calls `visible()` first: the offset only moves when the window is computed,
        so a hint read straight after `move()` reported the PREVIOUS scroll state.
        """
        self.visible()
        hidden_above = self.offset
        hidden_below = max(0, len(self.items) - self.offset - self.window)
        bits = []
        if hidden_above:
            bits.append(f"▲ {hidden_above} di atas")
        if hidden_below:
            bits.append(f"▼ {hidden_below} di bawah")
        return " · ".join(bits)

    # ------------------------------------------------------------------ #
    def move(self, delta: int) -> None:
        count = len(self.items)
        if not count:
            self.index = 0
            return
        self.index = max(0, min(count - 1, self.index + delta))

    def to_edge(self, *, top: bool) -> None:
        self.index = 0 if top else max(0, len(self.items) - 1)

    def selected(self) -> MenuItem | None:
        if not self.items or self.index >= len(self.items):
            return None
        return self.items[self.index]

    def enter(self) -> MenuItem | None:
        """Descend into the selected submenu. Returns the leaf item when there is none."""
        item = self.selected()
        if item is None:
            return None
        if item.is_submenu:
            self.stack.append(_Level(title=item.label, items=list(item.children)))
            self.index = 0
            self.offset = 0
            return None
        return item

    def back(self) -> bool:
        """Pop one level. False when already at the root (caller should close)."""
        if self.depth <= 1:
            return False
        self.stack.pop()
        self.index = 0
        self.offset = 0
        return True

    def index_at_row(self, row: int) -> int | None:
        """Map a rendered row (0-based, as the mouse reports it) to an item index.

        Clicks outside the item area return None so they cannot silently select a
        neighbouring entry.
        """
        if row < HEADER_ROWS:
            return None
        visible, offset = self.visible()
        position = row - HEADER_ROWS
        if position < 0 or position >= len(visible):
            return None
        return offset + position


# --------------------------------------------------------------------------- #
# building the command tree from the registry
# --------------------------------------------------------------------------- #
#: Verbs MEASURED to need an argument, taken from running each command with no
#: argument and watching what happened (see the verification pass in the commit
#: message). Static rules cannot tell `/help topic` (needs a topic) from
#: `/help full` (a complete command), so the exceptions are listed rather than
#: guessed -- and the menu pre-fills instead of submitting them.
NEEDS_ARGS_VERBS = frozenset({
    "query", "goal", "domain", "topic", "task_id", "edit", "run", "search",
    "install", "remove", "add", "set", "use", "plan", "replay", "ask", "attach",
})


def _subcommand_entries(usage: str) -> list[tuple[str, str, bool]]:
    """[(verb, full_entry, needs_args)] from the first bracket group."""
    start = usage.find("[")
    if start == -1:
        return []
    end = usage.find("]", start)
    if end <= start:
        return []
    entries: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for part in usage[start + 1:end].split("|"):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        head = tokens[0]
        if head.startswith("<") or head.endswith(">"):
            continue
        if head in seen:
            continue
        seen.add(head)
        # More than one token means the entry itself carries arguments; a single
        # token may still be a bare verb whose argument is implicit (`query`, `goal`).
        entries.append((head, part, len(tokens) > 1 or head.lstrip("-") in NEEDS_ARGS_VERBS))
    return entries


def _subcommands_from_usage(usage: str) -> list[str]:
    """`/config [show|set key=val]` -> ["show", "set"].

    Parsed from the usage text the registry already carries, so a new subcommand
    needs no second registration to appear in the menu.
    """
    start = usage.find("[")
    if start == -1:
        return []
    # The FIRST bracket group only: `[list|install] [args]` must not leak the
    # trailing `]` from the second group into the last subcommand (measured bug:
    # `/tools` advertised `doctor]`).
    end = usage.find("]", start)
    if end <= start:
        return []
    inner = usage[start + 1:end]
    words: list[str] = []
    for part in inner.split("|"):
        part = part.strip()
        if not part:
            continue
        # `set key=val` -> `set`; `<arg>`-style tokens are not subcommands.
        head = part.split()[0].strip()
        if head.startswith("<") or head.endswith(">"):
            continue
        if head not in words:
            words.append(head)
    return words


def build_command_menu(*, predicate: Callable[[Any], bool] | None = None) -> list[MenuItem]:
    """Level 0 = categories, level 1 = commands, level 2 = subcommands."""
    from collections import OrderedDict

    from wth_ai.platform.registry import get_platform_registry

    registry = get_platform_registry()
    commands = [c for c in getattr(registry, "commands", []) if predicate is None or predicate(c)]

    by_category: "OrderedDict[str, list[MenuItem]]" = OrderedDict()
    for cmd in sorted(commands, key=lambda c: (c.category, c.name)):
        children = [
            MenuItem(
                label=verb,
                description=f"{cmd.name} {entry}" + ("  (butuh argumen)" if needs_args else ""),
                # Trailing space = "the REPL must let me finish typing".
                action=f"{cmd.name} {verb} " if needs_args else f"{cmd.name} {verb}",
                needs_args=needs_args,
            )
            for verb, entry, needs_args in _subcommand_entries(getattr(cmd, "usage", "") or "")
        ]
        item = MenuItem(
            label=cmd.name,
            description=getattr(cmd, "description", "") or "",
            action=cmd.name,
            children=children,
        )
        by_category.setdefault(cmd.category or "lain", []).append(item)

    return [
        MenuItem(label=category, description=f"{len(items)} command", children=items)
        for category, items in by_category.items()
    ]


# --------------------------------------------------------------------------- #
# terminal layer
# --------------------------------------------------------------------------- #
def format_menu_rows(state: MenuState, *, width: int = 96) -> list[tuple[str, str]]:
    """Rendered rows as (style, text). Pure, so the look is testable."""
    rows: list[tuple[str, str]] = []
    rows.append(("class:menu-title", f" {state.breadcrumb()}"))
    hint = state.scroll_hint()
    rows.append(("class:menu-hint", f" {hint or ' '}"))
    visible, _offset = state.visible()
    if not visible:
        rows.append(("class:menu-dim", "  (kosong)"))
        return rows
    for position, item in enumerate(visible):
        real = state.offset + position
        selected = real == state.index
        marker = "▶" if selected else " "
        label = item.label
        description = item.description
        if description:
            room = max(10, width - len(label) - 8)
            text = f"{label:<22} {description[:room]}"
        else:
            text = label
        style = "class:menu-sel" if selected else "class:menu-item"
        suffix = " …" if item.is_submenu else ("  ⟨argumen⟩" if item.needs_args else "")
        rows.append((style, f" {marker} {text}{suffix}"))
    return rows


def build_menu_bindings(state: "MenuState", result: dict) -> Any:
    """Key bindings for the menu, built OUTSIDE the app so a test can construct
    them: an unparseable key (`"scroll-up"` instead of `Keys.ScrollUp`) only
    surfaced when a menu was opened for real, i.e. in the operator's REPL."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    bindings = KeyBindings()

    # Same eager wheel handling as the picker: scroll wherever the pointer is.
    @bindings.add(Keys.Vt100MouseEvent, eager=True)
    def _wheel_anywhere(event):
        from wth_ai.cli.ui.picker import wheel_delta

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

    # `Keys.ScrollUp`, not the string "scroll-up": the string form raises
    # `ValueError: Invalid key: scroll-up` while the app is being built, which
    # crashed the REPL the first time a menu was opened.
    @bindings.add("pageup")
    @bindings.add(Keys.ScrollUp)
    def _page_up(event):
        state.move(-state.window)
        event.app.invalidate()

    @bindings.add("pagedown")
    @bindings.add(Keys.ScrollDown)
    def _page_down(event):
        state.move(state.window)
        event.app.invalidate()

    @bindings.add("home")
    def _home(event):
        state.to_edge(top=True)
        event.app.invalidate()

    @bindings.add("end")
    def _end(event):
        state.to_edge(top=False)
        event.app.invalidate()

    @bindings.add("right")
    def _descend(event):
        item = state.enter()
        if item is not None:
            result["value"] = item.action
            event.app.exit()
        event.app.invalidate()

    @bindings.add("enter")
    def _accept(event):
        item = state.enter()
        if item is not None:
            result["value"] = item.action
            event.app.exit()

    @bindings.add("escape")
    @bindings.add("backspace")
    def _back(event):
        if not state.back():
            result["value"] = None
            event.app.exit()

    return bindings


def run_menu(
    items: Sequence[MenuItem],
    *,
    title: str = "Command",
    window: int = 12,
    mouse: bool = True,
) -> str | None:
    """Run the menu. Returns the selected ACTION text, or None when cancelled.

    Returns None at the root on Esc, and after backing out of submenus with Esc
    until the root is reached -- i.e. Esc never submits anything by accident.
    """
    if not items or not interactive_available():
        return None

    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.styles import Style

    from wth_ai.cli.ui.theme import THEME

    state = MenuState(items, title=title, window=window)
    result: dict[str, str | None] = {"value": None}

    def _render() -> FormattedText:
        out = FormattedText()
        for style, text in format_menu_rows(state):
            out.append((style, text + "\n"))
        out.append(("class:menu-hint",
                    " enter buka/pilih · esc kembali · ↑/↓ scroll · klik baris · scroll mouse"))
        return out

    bindings = build_menu_bindings(state, result)
    style = Style.from_dict({
        "menu-title": f"bold {THEME['brand']}",
        "menu-hint": THEME["muted"],
        "menu-dim": THEME["muted"],
        "menu-sel": f"bold {THEME['accent']} bg:{THEME['surface_alt']}",
        "menu-item": THEME["text"],
    })

    control = MenuControl(_render, state, result, enabled=mouse)
    app: Application = Application(
        layout=Layout(Window(control)),
        key_bindings=bindings,
        style=style,
        # Alternate screen ON (full_screen=True): terminals such as VTE
        # (GNOME Terminal / Kali's default) only forward wheel events to the app in
        # the alternate screen -- inline, the wheel scrolls the terminal's own
        # scrollback and the app never sees it. Measured symptom: the wheel worked
        # in tests (events injected directly) but not in the operator's terminal.
        full_screen=True,
        mouse_support=mouse,
    )
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return None
    return result["value"]


def parse_subcommands(usage: str) -> list[str]:
    """Public helper: subcommands a command advertises in its usage text."""
    return _subcommands_from_usage(usage)
