"""Tests for the interactive command menu.

What the operator asked for: `/` opens a menu they can WALK (keyboard), SCROLL
(wheel/arrows) and CLICK, submenus open on Enter (`/config` -> show/set), and Esc
goes back. Navigation, scrolling and the click->row mapping are pure logic here,
so they are tested without a terminal -- including a REAL click against the
control, which is how the `mouse_event.app` crash was caught (`MouseEvent` has no
`.app`; the app comes from `get_app()`).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.cli.ui.menu import (
    HEADER_ROWS,
    MenuItem,
    MenuState,
    build_command_menu,
    format_menu_rows,
    parse_subcommands,
)

TREE = [
    MenuItem(label="/config", description="lihat/ubah setting", children=[
        MenuItem(label="show", action="/config show"),
        MenuItem(label="set", action="/config set"),
    ]),
    MenuItem(label="/doctor", description="cek sistem", action="/doctor"),
    MenuItem(label="/help", description="bantuan", action="/help"),
    MenuItem(label="/jobs", description="daftar job", action="/jobs"),
    MenuItem(label="/plan", description="rencana", action="/plan"),
    MenuItem(label="/quit", description="keluar", action="/quit"),
]


# --------------------------------------------------------------------------- #
# usage parsing
# --------------------------------------------------------------------------- #
def test_subcommands_come_from_the_first_bracket_group():
    """Measured bug: `/tools [list|install|doctor] [args]` advertised `doctor]`."""
    assert parse_subcommands("/tools [list|install|doctor] [args]") == ["list", "install", "doctor"]
    assert parse_subcommands("/config [show|set key=val]") == ["show", "set"]
    assert parse_subcommands("/doctor") == []
    assert parse_subcommands("") == []


def test_placeholder_tokens_are_not_subcommands():
    assert parse_subcommands("/x [<arg>|run]") == ["run"]


# --------------------------------------------------------------------------- #
# navigation
# --------------------------------------------------------------------------- #
def test_move_clamps_at_both_ends():
    state = MenuState(TREE)
    state.move(-5)
    assert state.selected().label == "/config"
    state.move(999)
    assert state.selected().label == "/quit"


def test_enter_descends_into_a_submenu_and_returns_none():
    state = MenuState(TREE)
    assert state.enter() is None, "submenu tidak boleh langsung memilih"
    assert state.depth == 2
    assert [i.label for i in state.items] == ["show", "set"]


def test_enter_returns_the_leaf_action():
    state = MenuState(TREE)
    state.move(1)                     # /doctor
    item = state.enter()
    assert item is not None and item.action == "/doctor"


def test_esc_back_pops_a_level_and_stops_at_the_root():
    state = MenuState(TREE)
    state.enter()
    assert state.back() is True and state.depth == 1
    assert state.back() is False, "di root, back() harus bilang 'tutup menu'"


def test_breadcrumb_shows_the_path():
    state = MenuState(TREE, title="Command")
    state.enter()
    assert state.breadcrumb().startswith("Command › /config")


# --------------------------------------------------------------------------- #
# scrolling
# --------------------------------------------------------------------------- #
def test_window_scrolls_when_the_cursor_moves_past_it():
    state = MenuState(TREE, window=3)
    state.move(5)                     # jauh di bawah
    visible, offset = state.visible()
    assert len(visible) == 3
    assert offset > 0, "daftar tidak ikut scroll"
    assert offset <= state.index < offset + len(visible)


def test_scroll_hint_reports_hidden_rows():
    state = MenuState(TREE, window=3)
    assert "di bawah" in state.scroll_hint()
    state.move(5)
    assert "di atas" in state.scroll_hint()


def test_no_hint_when_everything_fits():
    assert MenuState(TREE, window=50).scroll_hint() == ""


def test_short_list_never_reports_hidden_rows():
    state = MenuState([MenuItem(label="only")], window=10)
    assert state.scroll_hint() == ""
    assert len(state.visible()[0]) == 1


# --------------------------------------------------------------------------- #
# mouse: row -> item mapping
# --------------------------------------------------------------------------- #
def test_click_maps_a_row_to_the_right_item():
    state = MenuState(TREE, window=6)
    assert state.index_at_row(HEADER_ROWS) == 0
    assert state.index_at_row(HEADER_ROWS + 2) == 2


def test_click_on_the_header_is_not_an_item():
    """Clicking the title row is 'back'; it must never select a neighbouring entry."""
    state = MenuState(TREE)
    for row in range(HEADER_ROWS):
        assert state.index_at_row(row) is None


def test_click_below_the_last_item_is_ignored():
    state = MenuState(TREE, window=3)
    assert state.index_at_row(HEADER_ROWS + 99) is None


def test_click_accounts_for_scrolling():
    state = MenuState(TREE, window=3)
    state.move(5)
    _visible, offset = state.visible()
    assert state.index_at_row(HEADER_ROWS) == offset, "klik harus ikut offset scroll"


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def test_selected_row_is_marked_and_styled():
    rows = format_menu_rows(MenuState(TREE))
    selected = [row for row in rows if row[0] == "class:menu-sel"]
    assert len(selected) == 1
    assert "▶" in selected[0][1] and "/config" in selected[0][1]


def test_submenu_rows_hint_they_open_something():
    rows = format_menu_rows(MenuState(TREE))
    config_row = next(text for _style, text in rows if "/config" in text)
    assert "…" in config_row


def test_rows_include_the_breadcrumb_and_a_scroll_hint():
    state = MenuState(TREE, window=3)
    rows = format_menu_rows(state)
    assert "Command" in rows[0][1]
    assert "di bawah" in rows[1][1]


def test_empty_menu_renders_something_readable():
    rows = format_menu_rows(MenuState([]))
    assert any("kosong" in text for _style, text in rows)


# --------------------------------------------------------------------------- #
# the built tree comes from the registry
# --------------------------------------------------------------------------- #
def test_command_tree_is_grouped_by_category_with_descriptions():
    tree = build_command_menu()
    assert len(tree) >= 5, "kategori tidak terbentuk"
    category = tree[0]
    assert category.is_submenu
    assert all(item.label.startswith("/") for item in category.children)
    assert all(item.description for item in category.children), "command tanpa deskripsi"


def test_command_tree_offers_subcommands_for_commands_that_have_them():
    tree = build_command_menu()
    items = [item for level in tree for item in level.children]
    config = next((i for i in items if i.label == "/config"), None)
    assert config is not None
    assert [c.label for c in config.children] == ["show", "set"]


# --------------------------------------------------------------------------- #
# REPL wiring
# --------------------------------------------------------------------------- #
def _repl():
    from wth_ai.cli.repl import WTHRepl

    return WTHRepl(lambda t: t)


def test_non_interactive_input_is_never_hijacked(monkeypatch):
    monkeypatch.setenv("WTH_PLAIN", "1")
    repl = _repl()
    assert repl._maybe_open_menu("/config") == "/config"
    assert repl._maybe_open_menu("halo biasa") == "halo biasa"


def test_command_with_arguments_is_left_alone(monkeypatch):
    """The menu is an aid, never a gate in front of a command already written."""
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.menu.run_menu", lambda *a, **k: "/config show")
    repl = _repl()
    assert repl._maybe_open_menu("/config show") == "/config show"
    assert repl._maybe_open_menu("/target example.com") == "/target example.com"


def test_bare_command_opens_its_submenu_and_returns_the_choice(monkeypatch):
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.menu.run_menu", lambda items, **k: "/config show")
    repl = _repl()
    assert repl._maybe_open_menu("/config") == "/config show"


def test_cancelling_the_menu_skips_the_turn(monkeypatch):
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.menu.run_menu", lambda items, **k: None)
    repl = _repl()
    assert repl._maybe_open_menu("/config") is None


def test_bare_slash_opens_the_whole_command_menu(monkeypatch):
    seen: dict = {}

    def _fake_run(items, **kwargs):
        seen["count"] = len(items)
        return "/help"

    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.menu.run_menu", _fake_run)
    repl = _repl()
    assert repl._maybe_open_menu("/") == "/help"
    assert seen["count"] >= 5, "menu '/' harus berisi kategori"


def test_unknown_command_is_not_intercepted(monkeypatch):
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    repl = _repl()
    assert repl._maybe_open_menu("/tidak-ada-command") == "/tidak-ada-command"


def test_question_mark_still_means_ask(monkeypatch):
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    repl = _repl()
    assert repl._maybe_open_menu("/config?") == "/config?"


# --------------------------------------------------------------------------- #
# mouse handler: a real click, against the real control
# --------------------------------------------------------------------------- #
def _click(row: int, *, kind=None):
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    return MouseEvent(position=Point(x=2, y=row),
                      event_type=kind or MouseEventType.MOUSE_UP,
                      button=MouseButton.LEFT, modifiers=frozenset())


class _FakeApp:
    def __init__(self):
        self.exited = False

    def invalidate(self):
        pass

    def exit(self):
        self.exited = True


def _control(monkeypatch, state, result, app):
    """Build the real MenuControl with the app seam patched."""
    import wth_ai.cli.ui.menu as menu_mod

    monkeypatch.setattr(menu_mod, "get_app", lambda: app, raising=False)
    return menu_mod.MenuControl(lambda: [], state, result)


def test_click_selects_then_activates(monkeypatch):
    """A real click against the real control.

    `MouseEvent` has no `.app`, so a handler reaching for one raised AttributeError
    on every click; this drives the handler exactly like prompt_toolkit does and is
    what caught it.
    """
    state = MenuState(TREE)
    result: dict = {"value": None}
    app = _FakeApp()
    control = _control(monkeypatch, state, result, app)

    control.mouse_handler(_click(HEADER_ROWS + 1))        # klik baris /doctor
    assert state.index == 1, "klik tidak memindahkan sorotan"
    assert result["value"] is None, "klik pertama hanya memilih"

    control.mouse_handler(_click(HEADER_ROWS + 1))        # klik kedua = aktifkan
    assert result["value"] == "/doctor"
    assert app.exited is True


def test_click_on_the_title_row_goes_back(monkeypatch):
    state = MenuState(TREE)
    result: dict = {"value": None}
    app = _FakeApp()
    control = _control(monkeypatch, state, result, app)

    control.mouse_handler(_click(0))
    assert state.depth == 1, "klik judul di root seharusnya menutup menu"
    assert app.exited is True and result["value"] is None

    # dari submenu, klik judul = kembali satu level (bukan menutup)
    state2 = MenuState(TREE)
    state2.enter()
    app2 = _FakeApp()
    result2: dict = {"value": None}
    control2 = _control(monkeypatch, state2, result2, app2)
    control2.mouse_handler(_click(0))
    assert state2.depth == 1 and app2.exited is False


def test_wheel_scrolls_without_activating(monkeypatch):
    from prompt_toolkit.mouse_events import MouseEventType

    state = MenuState(TREE)
    result: dict = {"value": None}
    app = _FakeApp()
    control = _control(monkeypatch, state, result, app)

    control.mouse_handler(_click(0, kind=MouseEventType.SCROLL_DOWN))
    assert state.index == 3, "roda mouse harus menggulir beberapa baris"
    assert result["value"] is None

    control.mouse_handler(_click(0, kind=MouseEventType.SCROLL_UP))
    assert state.index == 0


def test_click_outside_the_item_area_does_nothing(monkeypatch):
    state = MenuState(TREE, window=2)
    result: dict = {"value": None}
    app = _FakeApp()
    control = _control(monkeypatch, state, result, app)

    control.mouse_handler(_click(HEADER_ROWS + 50))
    assert state.index == 0 and result["value"] is None


# --------------------------------------------------------------------------- #
# bindings must be constructible
# --------------------------------------------------------------------------- #
def test_menu_bindings_can_be_built_and_include_scroll_keys():
    """`@bindings.add("scroll-up")` raises `ValueError: Invalid key` while the app
    is being built -- so the REPL crashed the first time a menu was opened. The
    enum form is asserted here."""
    from prompt_toolkit.keys import Keys

    from wth_ai.cli.ui.menu import build_menu_bindings

    bindings = build_menu_bindings(MenuState(TREE), {"value": None})
    keys = {key for binding in bindings.bindings for key in binding.keys}
    assert Keys.ScrollUp in keys and Keys.ScrollDown in keys
    # prompt_toolkit normalises these: "enter" -> ControlM, "escape" -> Escape.
    for expected, label in ((Keys.ControlM, "enter"), (Keys.Escape, "escape"),
                            (Keys.Up, "up"), (Keys.Down, "down"),
                            (Keys.Home, "home"), (Keys.End, "end")):
        assert expected in keys, f"binding {label!r} hilang"


def test_run_menu_returns_none_without_a_terminal(monkeypatch):
    """Non-tty must never block on a menu."""
    monkeypatch.setenv("WTH_PLAIN", "1")
    from wth_ai.cli.ui.menu import run_menu

    assert run_menu(TREE) is None


def test_bare_command_without_subcommands_gets_no_menu(monkeypatch):
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    repl = _repl()
    # /doctor punya usage tanpa bracket -> tidak ada menu, teks diteruskan apa adanya
    assert repl._maybe_open_menu("/doctor") == "/doctor"
