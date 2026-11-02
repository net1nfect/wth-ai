"""In-app pager: scroll recent output with the keyboard.

Why this exists (operator report: "after the recon, PageUp does nothing"):

A raw-mode CLI owns the keyboard. `wth` reads every key, so PageUp is delivered to
the APPLICATION, not to the terminal -- the terminal's own scrollback cannot move,
and after a turn finishes nothing changes because `wth` is still running. That is
true of every full-screen/raw-mode CLI (Codex, Claude Code, less, vim), so the fix
is not "release the terminal" but "provide the scroll".

The REPL therefore keeps a ring buffer of what it printed and opens this pager on
PageUp: arrows/PageUp/PageDown/Home/End (and the mouse wheel) move through it, Esc
or `q` closes and returns to the prompt exactly where it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

try:
    from prompt_toolkit.application.current import get_app
except ImportError:  # pragma: no cover - prompt_toolkit is a declared dependency
    def get_app() -> Any:  # type: ignore[misc]
        raise RuntimeError("prompt_toolkit tidak terpasang")


@dataclass
class PagerState:
    """Scroll position over a list of lines. Terminal-free so it can be tested."""

    lines: list[str]
    height: int = 20
    offset: int = field(default=0, init=False)

    @property
    def total(self) -> int:
        return len(self.lines)

    @property
    def content_height(self) -> int:
        """Lines available for CONTENT: the window minus the footer line.

        Measured: without this the footer overflowed whenever the content filled the
        window, so the pager showed text and no "esc/q keluar" bar.
        """
        return max(1, self.height - 1)

    @property
    def max_offset(self) -> int:
        return max(0, self.total - self.content_height)

    def move(self, delta: int) -> None:
        self.offset = max(0, min(self.max_offset, self.offset + delta))

    def to_edge(self, *, top: bool) -> None:
        self.offset = 0 if top else self.max_offset

    def visible(self) -> list[str]:
        return self.lines[self.offset:self.offset + self.content_height]

    def at_bottom(self) -> bool:
        return self.offset >= self.max_offset

    def status(self) -> str:
        if self.total <= self.content_height:
            return f"{self.total} baris"
        first = self.offset + 1
        last = min(self.total, self.offset + self.content_height)
        pct = int(100 * self.offset / max(1, self.max_offset))
        return f"{first}-{last} / {self.total} ({pct}%)"


def build_pager_bindings(state: "PagerState"):
    """Key bindings for the pager, outside the app so tests drive the SAME ones."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    from wth_ai.cli.ui.picker import wheel_delta

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

    # Terminal yang mengirim roda sebagai KEY (mode scroll-key), bukan event mouse.
    @bindings.add(Keys.ScrollUp)
    def _scroll_up(event):
        state.move(-3)
        event.app.invalidate()

    @bindings.add(Keys.ScrollDown)
    def _scroll_down(event):
        state.move(3)
        event.app.invalidate()

    @bindings.add("pageup")
    def _page_up(event):
        state.move(-state.content_height)
        event.app.invalidate()

    @bindings.add("pagedown")
    def _page_down(event):
        state.move(state.content_height)
        event.app.invalidate()

    @bindings.add("home")
    @bindings.add("c-home")
    def _home(event):
        state.to_edge(top=True)
        event.app.invalidate()

    @bindings.add("end")
    def _end(event):
        state.to_edge(top=False)
        event.app.invalidate()

    @bindings.add("escape")
    @bindings.add("q")
    @bindings.add("c-c")
    def _close(event):
        event.app.exit()

    # Wheel anywhere in the pager scrolls it (same eager trick as the picker: the
    # wheel must not depend on the pointer sitting over a particular row).
    @bindings.add(Keys.Vt100MouseEvent, eager=True)
    def _wheel(event):
        delta = wheel_delta(event.data)
        if not delta:
            return NotImplemented
        state.move(delta * 3)
        event.app.invalidate()
        return None

    return bindings


def footer_text(state: "PagerState", title: str) -> str:
    """The key-hint bar. Pure so its content is testable."""
    return f" {title} · {state.status()} · esc/q keluar · ↑/↓/pgup/pgdn/scroll"


def show_pager(lines: Sequence[str], *, title: str = "output") -> None:
    """Open the pager. Returns when the operator closes it (Esc/q)."""
    content = [line.rstrip("\n") for line in lines]
    if not content:
        return

    import shutil

    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    from wth_ai.cli.ui.picker import wheel_delta
    from wth_ai.cli.ui.theme import THEME

    rows = max(5, shutil.get_terminal_size((100, 30)).lines - 3)
    state = PagerState(lines=list(content), height=rows)

    def _content() -> FormattedText:
        # Ukuran diambil dari APP, bukan dari shutil: aritmetika padding manual
        # bergantung pada tebakan tinggi terminal dan footer-nya jatuh di luar
        # jendela (terukur: konten tampil, "esc/q keluar" tidak pernah tertulis).
        try:
            state.height = max(3, get_app().output.get_size().rows - 1)
        except Exception:  # noqa: BLE001
            pass
        out = FormattedText()
        for line in state.visible():
            out.append(("class:body", line + "\n"))
        return out

    def _footer() -> FormattedText:
        return FormattedText([("class:footer", footer_text(state, title))])

    bindings = build_pager_bindings(state)
    style = Style.from_dict({
        "body": THEME["text"],
        "footer": f"bold {THEME['brand']} bg:{THEME['surface_alt']}",
    })

    app = build_pager_app(state, bindings=bindings, style=style, render=_content, footer=_footer)
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return


def build_pager_app(state: "PagerState", *, bindings, style, render=None, footer=None, **kwargs):
    """The pager Application, built here so a test can drive it with a pipe input.

    The footer is its own one-row Window in an HSplit. Padding the content to leave
    room for it depended on guessing the terminal height, and the guess was wrong:
    the live pager showed the content and never the key hints.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    control = FormattedTextControl(render, focusable=True)
    body = [Window(control)]
    if footer is not None:
        body.append(Window(FormattedTextControl(footer), height=1))
    return Application(
        layout=Layout(HSplit(body)),
        key_bindings=bindings,
        style=style,
        full_screen=True,      # alternate screen: no clutter, and VTE forwards the wheel
        mouse_support=True,
        **kwargs,
    )


def collect_output(buffer: Iterable[str], *, limit: int = 4000) -> list[str]:
    """Plain-text lines for the pager, oldest first, capped to `limit` lines."""
    lines = list(buffer)
    return lines[-limit:] if len(lines) > limit else lines
