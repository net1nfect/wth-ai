"""Tests for the in-app pager (PageUp at the prompt).

Operator report: "after the recon, PageUp does nothing". Cause is structural, not a
bug: a raw-mode CLI owns the keyboard, so PageUp goes to the APPLICATION and the
terminal's scrollback never moves -- true of every raw-mode app (less, vim, Codex).
The REPL therefore keeps a ring buffer of what it printed and serves PageUp itself.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.cli.ui.pager import PagerState, collect_output


# --------------------------------------------------------------------------- #
# scroll state
# --------------------------------------------------------------------------- #
def test_short_content_needs_no_scrolling():
    state = PagerState(lines=["a", "b"], height=10)
    assert state.max_offset == 0
    state.move(5)
    assert state.offset == 0, "konten pendek tidak boleh bisa digulir"
    assert state.at_bottom() is True
    assert "baris" in state.status()


def test_scrolling_clamps_at_both_ends():
    state = PagerState(lines=[f"line {i}" for i in range(100)], height=10)
    assert state.max_offset == 91
    state.move(-5)
    assert state.offset == 0
    state.move(1000)
    assert state.offset == 91
    assert state.to_edge(top=True) is None and state.offset == 0
    state.to_edge(top=False)
    assert state.offset == 91


def test_visible_window_follows_the_offset():
    # height=5 -> 4 baris konten + 1 baris footer
    state = PagerState(lines=[f"line {i}" for i in range(50)], height=5)
    assert state.visible() == [f"line {i}" for i in range(4)]
    state.move(10)
    assert state.visible()[0] == "line 10"
    assert len(state.visible()) == 4


def test_status_reports_position_and_percentage():
    state = PagerState(lines=[f"l{i}" for i in range(100)], height=10)
    assert "1-9 / 100 (0%)" in state.status()
    state.to_edge(top=False)
    assert "92-100 / 100 (100%)" in state.status()


def test_empty_lines_do_not_crash():
    state = PagerState(lines=[], height=10)
    assert state.visible() == []
    state.move(1)
    assert state.offset == 0


def test_collect_output_keeps_the_newest_lines():
    buffer = [f"line {i}" for i in range(50)]
    kept = collect_output(buffer, limit=10)
    assert len(kept) == 10
    assert kept[-1] == "line 49", "yang dibuang harus yang paling lama"


# --------------------------------------------------------------------------- #
# bindings exist and the app runs
# --------------------------------------------------------------------------- #
def test_bindings_cover_navigation_and_close():
    from prompt_toolkit.keys import Keys

    from wth_ai.cli.ui.pager import build_pager_bindings

    bindings = build_pager_bindings(PagerState(lines=["a"], height=3))
    keys = {key for binding in bindings.bindings for key in binding.keys}
    for expected, label in ((Keys.Up, "up"), (Keys.Down, "down"), (Keys.PageUp, "pageup"),
                            (Keys.PageDown, "pagedown"), (Keys.Home, "home"), (Keys.End, "end"),
                            (Keys.Escape, "escape"), (Keys.ScrollUp, "scroll-key-up"),
                            (Keys.ScrollDown, "scroll-key-down"),
                            # roda mouse sungguhan datang sebagai ini, bukan ScrollUp
                            (Keys.Vt100MouseEvent, "wheel-event")):
        assert expected in keys, f"binding {label} hilang"
    assert "q" in keys


def test_pager_app_is_full_screen_with_mouse():
    from prompt_toolkit.output import DummyOutput

    from wth_ai.cli.ui.pager import build_pager_app, build_pager_bindings

    state = PagerState(lines=["a", "b"], height=5)
    app = build_pager_app(state, bindings=build_pager_bindings(state), style=None,
                          render=lambda: [], output=DummyOutput())
    assert app.full_screen is True
    assert bool(app.mouse_support()) is True


def test_real_keys_scroll_the_pager_through_a_pipe():
    """PageDown / PageUp / q driven as real escape sequences."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from wth_ai.cli.ui.pager import build_pager_app, build_pager_bindings

    state = PagerState(lines=[f"line {i}" for i in range(100)], height=10)
    pipe_cm = create_pipe_input()
    pipe = pipe_cm.__enter__()
    app = build_pager_app(state, bindings=build_pager_bindings(state), style=None,
                          render=lambda: [], input=pipe, output=DummyOutput())
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(0.4)
    try:
        # Halaman = tinggi jendela - 1 baris footer (height=10 -> 9 baris).
        pipe.send_text("\x1b[6~")            # PageDown
        time.sleep(0.3)
        assert state.offset == 9, f"PageDown tidak menggulir: {state.offset}"
        pipe.send_text("\x1b[6~")
        time.sleep(0.3)
        assert state.offset == 18
        pipe.send_text("\x1b[5~")            # PageUp
        time.sleep(0.3)
        assert state.offset == 9
        pipe.send_text("\x1b[B")             # panah bawah
        time.sleep(0.3)
        assert state.offset == 10
    finally:
        pipe.send_text("q")
        thread.join(timeout=2)
        pipe_cm.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# REPL wiring
# --------------------------------------------------------------------------- #
def test_turn_output_lands_in_the_buffer():
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    repl._remember_output("/doctor", "baris satu\nbaris dua")
    buffered = list(repl._output_buffer)
    assert any("/doctor" in line for line in buffered)
    assert "baris satu" in buffered and "baris dua" in buffered


def test_buffer_is_bounded():
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    for i in range(6000):
        repl._output_buffer.append(f"l{i}")
    assert len(repl._output_buffer) == 4000


def test_pageup_is_bound_to_the_pager():
    from wth_ai.cli.repl import WTHRepl
    from prompt_toolkit.keys import Keys

    repl = WTHRepl(lambda t: t)
    keys = {key for binding in repl.bindings.bindings for key in binding.keys}
    assert Keys.PageUp in keys, "PageUp tidak terhubung ke pager"
    assert "c-o" in keys or any(str(k) == "c-o" for k in keys)


def test_pager_is_requested_not_run_from_the_binding():
    """The binding must NOT launch the pager itself.

    Measured crash: `run_in_terminal(self._open_pager)` inside the live prompt loop
    raised "asyncio.run() cannot be called from a running event loop". The binding
    now sets a flag and exits the prompt; the run loop opens the pager.
    """
    import inspect

    from wth_ai.cli import repl as repl_mod

    source = inspect.getsource(repl_mod.WTHRepl._make_bindings)
    assert "_pager_requested" in source, "binding tidak memakai flag"
    assert "run_in_terminal(self._open_pager)" not in source, "binding masih menjalankan pager langsung"

    run_source = inspect.getsource(repl_mod.WTHRepl.run)
    assert "self._open_pager()" in run_source, "loop tidak membuka pager"


def test_footer_text_reports_position_and_keys():
    from wth_ai.cli.ui.pager import footer_text

    state = PagerState(lines=[f"l{i}" for i in range(100)], height=10)
    text = footer_text(state, "output")
    assert "esc/q keluar" in text and "output" in text
    assert "1-9 / 100" in text, text


def test_pager_layout_gives_the_footer_its_own_visible_row():
    """The footer must be a real widget row.

    Regression: it used to be the last line of a manually padded string, and the
    padding depended on a terminal-height guess that was wrong -- the live pager
    rendered the content and no key hints.
    """
    from prompt_toolkit.layout import HSplit, Window
    from prompt_toolkit.output import DummyOutput

    from wth_ai.cli.ui.pager import build_pager_app, build_pager_bindings

    state = PagerState(lines=["a"], height=5)
    app = build_pager_app(state, bindings=build_pager_bindings(state), style=None,
                          render=lambda: [], footer=lambda: [], output=DummyOutput())
    container = app.layout.container
    assert isinstance(container, HSplit)
    windows = [w for w in container.get_children() if isinstance(w, Window)]
    assert len(windows) == 2, "pager harus punya window konten + window footer"
    assert windows[1].height is not None and windows[1].height == 1


def test_real_keys_scroll_the_pager_through_a_pipe():
    """PageDown / PageUp / q driven as real escape sequences."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from wth_ai.cli.ui.pager import build_pager_app, build_pager_bindings

    state = PagerState(lines=[f"line {i}" for i in range(100)], height=10)
    pipe_cm = create_pipe_input()
    pipe = pipe_cm.__enter__()
    app = build_pager_app(state, bindings=build_pager_bindings(state), style=None,
                          render=lambda: [], input=pipe, output=DummyOutput())
    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(0.4)
    try:
        # Halaman = tinggi jendela - 1 baris footer (height=10 -> 9 baris).
        pipe.send_text("\x1b[6~")            # PageDown
        time.sleep(0.3)
        assert state.offset == 9, f"PageDown tidak menggulir: {state.offset}"
        pipe.send_text("\x1b[6~")
        time.sleep(0.3)
        assert state.offset == 18
        pipe.send_text("\x1b[5~")            # PageUp
        time.sleep(0.3)
        assert state.offset == 9
        pipe.send_text("\x1b[B")             # panah bawah
        time.sleep(0.3)
        assert state.offset == 10
    finally:
        pipe.send_text("q")
        thread.join(timeout=2)
        pipe_cm.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# REPL wiring
# --------------------------------------------------------------------------- #
def test_turn_output_lands_in_the_buffer():
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    repl._remember_output("/doctor", "baris satu\nbaris dua")
    buffered = list(repl._output_buffer)
    assert any("/doctor" in line for line in buffered)
    assert "baris satu" in buffered and "baris dua" in buffered


def test_buffer_is_bounded():
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    for i in range(6000):
        repl._output_buffer.append(f"l{i}")
    assert len(repl._output_buffer) == 4000


def test_pageup_is_bound_to_the_pager():
    from wth_ai.cli.repl import WTHRepl
    from prompt_toolkit.keys import Keys

    repl = WTHRepl(lambda t: t)
    keys = {key for binding in repl.bindings.bindings for key in binding.keys}
    assert Keys.PageUp in keys, "PageUp tidak terhubung ke pager"
    assert "c-o" in keys or any(str(k) == "c-o" for k in keys)


def test_pager_is_requested_not_run_from_the_binding():
    """The binding must NOT launch the pager itself.

    Measured crash: `run_in_terminal(self._open_pager)` inside the live prompt loop
    raised "asyncio.run() cannot be called from a running event loop". The binding
    now sets a flag and exits the prompt; the run loop opens the pager.
    """
    import inspect

    from wth_ai.cli import repl as repl_mod

    source = inspect.getsource(repl_mod.WTHRepl._make_bindings)
    assert "_pager_requested" in source, "binding tidak memakai flag"
    assert "run_in_terminal(self._open_pager)" not in source, "binding masih menjalankan pager langsung"

    run_source = inspect.getsource(repl_mod.WTHRepl.run)
    assert "self._open_pager()" in run_source, "loop tidak membuka pager"



