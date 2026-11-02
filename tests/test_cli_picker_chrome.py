"""Tests for the interactive model picker and the chat chrome.

The picker LOGIC is a plain object on purpose -- window off-by-ones, a filter that
forgets to reset the cursor, and a selection that disagrees with the highlighted
row are exactly the bugs a TUI hides until a user hits them. Those are tested
here without a terminal; the terminal layer is exercised through its degradations
(non-tty must never block) and the chrome through its rendered strings.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.cli.ui.picker import ListPickerState, interactive_available, pick_from_list

CATALOG = [f"model-{i:03d}" for i in range(50)] + ["glm-5.3-flash-rr", "glm-4.6-air"]


# --------------------------------------------------------------------------- #
# filter + cursor
# --------------------------------------------------------------------------- #
def test_filter_narrows_and_resets_the_cursor():
    state = ListPickerState(items=CATALOG)
    state.move(30)
    assert state.selected() == CATALOG[30]
    state.set_query("glm")
    # Cursor was pointing at index 30 of a 52-item list; after filtering it must
    # anchor at the top or it would highlight a different model than the operator
    # was looking at.
    assert state.index == 0
    assert state.selected() == "glm-5.3-flash-rr"


def test_filter_is_case_insensitive_substring():
    state = ListPickerState(items=CATALOG)
    state.set_query("GLM-4")
    assert state.matches == ["glm-4.6-air"]


def test_movement_clamps_instead_of_wrapping():
    state = ListPickerState(items=["a", "b", "c"])
    state.move(-1)
    assert state.selected() == "a", "cursor tidak boleh membungkus ke bawah"
    state.move(99)
    assert state.selected() == "c", "cursor tidak boleh membungkus ke atas"
    # Holding an arrow key one press too long must not silently select the other end.
    state.move(1)
    assert state.selected() == "c"


def test_selection_always_matches_the_highlighted_row():
    state = ListPickerState(items=CATALOG, window=5)
    for expected_index in (0, 1, 7, 12, len(CATALOG) - 1):
        state.index = expected_index
        visible, offset = state.window_slice()
        assert state.selected() == CATALOG[expected_index]
        # The highlighted row is inside the visible window at the right place.
        if visible:
            assert offset <= expected_index < offset + len(visible)


def test_window_keeps_selection_visible_while_scrolling():
    state = ListPickerState(items=CATALOG, window=4)
    for idx in range(0, 20):
        state.index = idx
        visible, offset = state.window_slice()
        assert len(visible) <= 4
        assert offset <= idx < offset + len(visible), (idx, offset, visible)


def test_empty_filter_result_is_not_a_crash():
    state = ListPickerState(items=CATALOG)
    state.set_query("tidak-ada-apa-pun")
    assert state.matches == []
    assert state.selected() is None
    assert state.window_slice() == ([], 0)
    assert "0 dari" in state.status()
    state.move(1)
    assert state.selected() is None


def test_status_reports_position_and_totals():
    state = ListPickerState(items=CATALOG)
    assert "52 total" in state.status()
    state.set_query("glm")
    assert "1/2 cocok" in state.status()


# --------------------------------------------------------------------------- #
# degradation: never block where nobody can see it
# --------------------------------------------------------------------------- #
def test_non_tty_is_not_interactive(monkeypatch):
    class _Pipe:
        def isatty(self) -> bool:
            return False

    import sys

    monkeypatch.setattr(sys, "stdin", _Pipe())
    monkeypatch.setattr(sys, "stdout", _Pipe())
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    monkeypatch.delenv("WTH_NO_PICKER", raising=False)
    assert interactive_available() is False


def test_env_can_disable_the_picker(monkeypatch):
    class _Tty:
        def isatty(self) -> bool:
            return True

    import sys

    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.setenv("WTH_NO_PICKER", "1")
    assert interactive_available() is False
    monkeypatch.delenv("WTH_NO_PICKER")
    monkeypatch.setenv("WTH_PLAIN", "1")
    assert interactive_available() is False


def test_picker_returns_none_without_a_terminal(monkeypatch):
    class _Pipe:
        def isatty(self) -> bool:
            return False

    import sys

    monkeypatch.setattr(sys, "stdin", _Pipe())
    monkeypatch.setattr(sys, "stdout", _Pipe())
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    assert pick_from_list("t", ["a", "b"]) is None


def test_picker_returns_none_for_an_empty_list():
    assert pick_from_list("t", []) is None


# --------------------------------------------------------------------------- #
# slash wiring
# --------------------------------------------------------------------------- #
class _Orch:
    """Minimal orchestrator for the picker flow (no terminal)."""

    def __init__(self):
        from wth_ai.config import ProviderRoleConfig

        class _Models:
            planner = "auto"
            recon = "auto"
            executor = "auto"
            code = "auto"
            verifier = "auto"
            reporter = "auto"
            fallback = "auto"

        class _Cfg:
            def __init__(self):
                self.providers = {"planner": ProviderRoleConfig(type="custom", model="auto")}
                # `_model_show` falls back to config.models.<role> for roles without
                # a provider entry; a stub missing it fails the test for the wrong
                # reason (the real config always has it).
                self.models = _Models()

            def save_user_config(self):
                pass

        class _Client:
            def list_models(self, provider_type):
                return list(CATALOG)

            def provider_for_role(self, role):
                return type("P", (), {"name": "custom", "complete": staticmethod(lambda *a, **k: {})})()

        class _State:
            metadata: dict = {}

        self.provider = _Client()
        self.config = _Cfg()
        self.state = _State()
        self.client = self.provider

    def _persist(self):
        pass


def test_bare_model_command_falls_back_to_text_without_a_terminal(monkeypatch):
    monkeypatch.setenv("WTH_PLAIN", "1")
    from wth_ai.cli.handlers.setup_handler import handle_model

    out = handle_model(_Orch(), [])
    assert "Models (configured)" in out
    assert "/model pick" in out, "harus memberi tahu cara memilih interaktif"


def test_model_pick_falls_back_to_the_listing_without_a_terminal(monkeypatch):
    monkeypatch.setenv("WTH_PLAIN", "1")
    from wth_ai.cli.handlers.setup_handler import handle_model

    orch = _Orch()
    out = handle_model(orch, ["pick", "glm"])
    assert "glm-5.3-flash-rr" in out
    assert orch.state.metadata["model_picker"]["models"] == ["glm-5.3-flash-rr", "glm-4.6-air"]


def test_model_pick_cancel_changes_nothing(monkeypatch):
    """Cancelling the picker (Esc) must leave the config untouched."""
    monkeypatch.setenv("WTH_PLAIN", "1")
    monkeypatch.setattr("wth_ai.cli.ui.picker.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.picker.pick_from_list", lambda *a, **k: None)
    from wth_ai.cli.handlers.setup_handler import handle_model

    orch = _Orch()
    out = handle_model(orch, ["pick"])
    assert "Dibatalkan" in out
    assert orch.config.providers["planner"].model == "auto"


def test_model_pick_applies_the_choice_and_offers_set_all(monkeypatch):
    monkeypatch.setenv("WTH_SKIP_MODEL_VERIFY", "1")
    monkeypatch.setattr("wth_ai.cli.ui.picker.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.picker.pick_from_list",
                        lambda *a, **k: "glm-5.3-flash-rr")
    from wth_ai.cli.handlers.setup_handler import handle_model

    orch = _Orch()
    out = handle_model(orch, ["pick"])
    assert orch.config.providers["planner"].model == "glm-5.3-flash-rr"
    assert "/model set all glm-5.3-flash-rr" in out


# --------------------------------------------------------------------------- #
# chrome: status line, help line, shortcuts panel
# --------------------------------------------------------------------------- #
def _repl():
    from wth_ai.cli.repl import WTHRepl

    return WTHRepl(lambda t: t)


def test_toolbar_is_two_lines_status_then_help():
    segs = _repl()._toolbar()
    text = "".join(s for _, s in segs)
    assert "\n" in text, "toolbar harus dua baris (status + bantuan)"
    status, _, help_line = text.partition("\n")
    assert "← for agents" in help_line and "? for shortcuts" in help_line
    assert status.strip(), "baris status tidak boleh kosong"


def test_status_line_carries_model_target_and_permission():
    class _Scope:
        target = "example.com"

    class _Perm:
        value = "standard"

    class _State:
        scope = _Scope()
        permission_mode = _Perm()

    class _Client:
        def model_for_role(self, role):
            return ("glm-5.3-flash-rr", "")

        def provider_for_role(self, role):
            return type("P", (), {"name": "custom"})()

    class _Orch:
        state = _State()
        client = _Client()

    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t, orchestrator=_Orch())
    text = "".join(s for _, s in repl._status_line())
    assert "glm-5.3-flash-rr" in text
    assert "example.com" in text
    assert "standard" in text


def test_status_line_survives_a_broken_orchestrator():
    class _Boom:
        @property
        def state(self):
            raise RuntimeError("boom")

    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t, orchestrator=_Boom())
    assert "".join(s for _, s in repl._status_line()).strip()


def test_shortcuts_panel_lists_the_keys(capsys):
    _repl()._print_shortcuts()
    out = capsys.readouterr().out
    for token in ("enter", "ctrl+d", "ctrl+r", "?"):
        assert token in out


def test_agents_view_lists_roles(capsys):
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    repl._print_agents()
    out = capsys.readouterr().out
    assert "planner" in out and "executor" in out


def test_key_bindings_include_question_mark_and_left():
    from prompt_toolkit.keys import Keys

    repl = _repl()
    keys = {key for binding in repl.bindings.bindings for key in binding.keys}
    assert "?" in keys, "binding '?' tidak terdaftar"
    # Kilah `Keys.Left`, bukan pencocokan string: prompt_toolkit menyimpan panah
    # sebagai enum, jadi mencari substring "left" selalu gagal.
    assert Keys.Left in keys, "binding panah kiri tidak terdaftar"


# --------------------------------------------------------------------------- #
# picker look: highlighted row + one honest description line per entry
# --------------------------------------------------------------------------- #
def test_selected_row_is_marked_and_styled():
    from wth_ai.cli.ui.picker import format_rows

    state = ListPickerState(items=["alpha", "beta", "gamma"])
    state.index = 1
    rows = format_rows(state)
    selected = [row for row in rows if row[0] == "class:sel"]
    assert len(selected) == 1
    assert "▶" in selected[0][1] and "beta" in selected[0][1]


def test_description_is_rendered_under_its_own_row():
    from wth_ai.cli.ui.picker import format_rows

    rows = format_rows(ListPickerState(items=["m1"]), describe=lambda m: "catatan singkat")
    assert [style for style, _ in rows] == ["class:sel", "class:desc"]
    assert "catatan singkat" in rows[1][1]


def test_a_blank_description_adds_no_row():
    from wth_ai.cli.ui.picker import format_rows

    rows = format_rows(ListPickerState(items=["m1"]), describe=lambda m: "")
    assert len(rows) == 1


def test_description_marks_the_model_in_use_and_probe_results():
    from wth_ai.cli.ui.picker import describe_model

    assert "sedang dipakai" in describe_model("glm-x", current="glm-x")
    assert "lolos uji" in describe_model("glm-x", tested={"glm-x": True})
    assert "gagal uji di sesi ini" in describe_model("glm-x", tested={"glm-x": False})


def test_description_reports_only_facts_from_the_id():
    from wth_ai.cli.ui.picker import describe_model

    text = describe_model("z-ai/glm-5.3:free")
    assert "vendor z-ai" in text and "free" in text


def test_description_never_invents_quality_claims():
    """The reference UI says "fast and affordable"; we may not, because we have not
    measured that. Only observed facts are allowed here."""
    from wth_ai.cli.ui.picker import describe_model

    text = describe_model("z-ai/glm-5.3:free", current="other",
                          tested={"z-ai/glm-5.3:free": True}).lower()
    for invented in ("cepat", "murah", "powerful", "terbaik", "affordable", "balanced",
                     "pintar", "tercepat"):
        assert invented not in text, f"klaim kualitas tanpa bukti: {invented}"


def test_picker_footer_matches_the_reference_wording():
    import inspect

    from wth_ai.cli.ui import picker as mod

    source = inspect.getsource(mod)
    assert "enter select" in source and "esc back" in source


# --------------------------------------------------------------------------- #
# picker mouse: wheel (touchpad) + click
# --------------------------------------------------------------------------- #
def _mouse(row: int, *, kind=None):
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    return MouseEvent(position=Point(x=3, y=row), event_type=kind or MouseEventType.MOUSE_UP,
                      button=MouseButton.LEFT, modifiers=frozenset())


class _FakeApp:
    def __init__(self):
        self.exited = False

    def invalidate(self):
        pass

    def exit(self):
        self.exited = True


def _picker_control(monkeypatch, state, result, *, per_item=1, app=None):
    import wth_ai.cli.ui.picker as picker_mod

    app = app or _FakeApp()
    monkeypatch.setattr(picker_mod, "get_app", lambda: app, raising=False)
    return picker_mod.build_picker_control(state, result, per_item=per_item)(lambda: [], focusable=True), app


def test_picker_wheel_scrolls(monkeypatch):
    """The complaint was scroll-by-touchpad doing nothing; the wheel arrives as
    SCROLL_* mouse events, which had NO handler in the picker at all."""
    from prompt_toolkit.mouse_events import MouseEventType

    state = ListPickerState(items=[f"m{i}" for i in range(40)], window=8)
    result: dict = {"value": None}
    control, _app = _picker_control(monkeypatch, state, result)

    control.mouse_handler(_mouse(0, kind=MouseEventType.SCROLL_DOWN))
    assert state.selected() == "m3", f"roda mouse tidak menggulir (index={state.index})"
    control.mouse_handler(_mouse(0, kind=MouseEventType.SCROLL_UP))
    assert state.selected() == "m0"


def test_picker_click_selects_then_accepts(monkeypatch):
    import wth_ai.cli.ui.picker as picker_mod

    state = ListPickerState(items=[f"m{i}" for i in range(10)], window=6)
    result: dict = {"value": None}
    control, app = _picker_control(monkeypatch, state, result)

    control.mouse_handler(_mouse(picker_mod.PICKER_HEADER_ROWS + 2))
    assert state.selected() == "m2", "klik tidak memindahkan sorotan"
    assert result["value"] is None

    control.mouse_handler(_mouse(picker_mod.PICKER_HEADER_ROWS + 2))
    assert result["value"] == "m2" and app.exited


def test_picker_click_on_a_description_row_is_ignored(monkeypatch):
    import wth_ai.cli.ui.picker as picker_mod

    state = ListPickerState(items=[f"m{i}" for i in range(6)], window=4)
    result: dict = {"value": None}
    control, _app = _picker_control(monkeypatch, state, result, per_item=2)

    # header + 1 (baris deskripsi item pertama) -> bukan target
    control.mouse_handler(_mouse(picker_mod.PICKER_HEADER_ROWS + 1))
    assert state.index == 0 and result["value"] is None


def test_picker_click_on_title_does_not_cancel(monkeypatch):
    """A click at/above the header must not close the picker.

    Measured: the Window mouse-wrapper clamps a click outside the rendered content
    to the last visible line, so clicks can arrive as row 0. When row 0 meant
    "cancel", every click closed the model list. Cancel is Esc's job.
    """
    import wth_ai.cli.ui.picker as picker_mod

    state = ListPickerState(items=["a", "b"])
    result: dict = {"value": None}
    control, app = _picker_control(monkeypatch, state, result)
    control.mouse_handler(_mouse(picker_mod.PICKER_HEADER_ROWS - 1))
    assert result["value"] is None and app.exited is False


def test_picker_click_below_the_list_is_ignored(monkeypatch):
    import wth_ai.cli.ui.picker as picker_mod

    state = ListPickerState(items=["a", "b"], window=4)
    result: dict = {"value": None}
    control, app = _picker_control(monkeypatch, state, result)
    control.mouse_handler(_mouse(picker_mod.PICKER_HEADER_ROWS + 40))
    assert result["value"] is None and app.exited is False


def test_picker_app_enables_mouse():
    """Without mouse_support the terminal never sends the events at all.

    Asserts the built Application's flag rather than a string in some function body:
    the construction moved into `build_picker_app`, and a source-text assertion
    silently stopped meaning anything.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.output import DummyOutput

    from wth_ai.cli.ui.picker import ListPickerState, build_picker_app

    app = build_picker_app(ListPickerState(items=["a"]), {"value": None},
                           bindings=KeyBindings(), style=None, output=DummyOutput())
    assert bool(app.mouse_support()) is True


# --------------------------------------------------------------------------- #
# /model must reach its own picker, not the generic submenu
# --------------------------------------------------------------------------- #
def test_bare_model_command_is_not_hijacked_by_the_subcommand_menu(monkeypatch):
    """Measured complaint: `/model` showed list/use/set/fix/show instead of models."""
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr("wth_ai.cli.ui.menu.run_menu",
                        lambda *a, **k: calls.append("menu") or "/model list")
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    assert repl._maybe_open_menu("/model") == "/model"
    assert calls == [], "menu subcommand masih mengintersep /model"


def test_model_with_explicit_subcommand_is_also_left_alone(monkeypatch):
    monkeypatch.setattr("wth_ai.cli.ui.menu.interactive_available", lambda: True)
    monkeypatch.setattr("wth_ai.cli.ui.menu.run_menu", lambda *a, **k: "SHOULD-NOT-BE-USED")
    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    assert repl._maybe_open_menu("/model show") == "/model show"


# --------------------------------------------------------------------------- #
# wheel + click driven through a REAL input pipe
#
# The synthetic-MouseEvent tests above exercise the handler; these feed actual
# terminal escape sequences into the app, which is what a touchpad produces. They
# exist because a pty run made the wheel look broken when the coordinates were
# simply outside the list.
# --------------------------------------------------------------------------- #
def _drive_picker(items, *, window=8, per_item=1, keys_after=0.25):
    """Run the picker against a pipe input; yields (state, result, send, done)."""
    import threading
    import time

    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from wth_ai.cli.ui.picker import ListPickerState, build_picker_app, build_picker_bindings

    state = ListPickerState(items=items, window=window)
    result: dict = {"value": None}
    ctx = {}
    # `create_pipe_input()` is a context manager; the pipe is only usable once
    # entered (otherwise: AttributeError ... has no attribute 'typeahead_hash').
    pipe_cm = create_pipe_input()
    pipe = pipe_cm.__enter__()
    app = build_picker_app(state, result, bindings=build_picker_bindings(state, result),
                           style=None, per_item=per_item, input=pipe, output=DummyOutput())
    ctx["pipe"] = pipe
    ctx["pipe_cm"] = pipe_cm

    def _runner():
        app.run()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    time.sleep(0.4)
    ctx["state"] = state
    ctx["result"] = result
    ctx["app_running"] = lambda: thread.is_alive()
    ctx["sleep"] = time.sleep
    def _stop():
        try:
            pipe.send_text("\x1b")
        except Exception:  # noqa: BLE001
            pass
        thread.join(timeout=3)
        try:
            ctx["pipe_cm"].__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

    ctx["stop"] = _stop
    return ctx


def test_touchpad_wheel_actually_scrolls_the_model_list():
    """Reported: "gabisa di scroll make touchpad". A two-finger scroll arrives as
    real SGR mouse sequences; this feeds exactly those."""
    ctx = _drive_picker([f"model-{i:03d}" for i in range(50)])
    state, pipe = ctx["state"], ctx["pipe"]
    try:
        assert state.selected() == "model-000"
        pipe.send_text("\x1b[<65;10;12M")          # wheel down
        ctx["sleep"](0.35)
        assert state.selected() == "model-003", f"roda tidak menggulir: {state.selected()}"
        pipe.send_text("\x1b[<65;10;12M")
        ctx["sleep"](0.35)
        assert state.selected() == "model-006"
        pipe.send_text("\x1b[<64;10;12M")          # wheel up
        ctx["sleep"](0.35)
        assert state.selected() == "model-003"
    finally:
        ctx["stop"]()


def test_item_click_handler_selects_then_activates(monkeypatch):
    """The click path, tested without screen coordinates.

    Coordinate hit-testing cannot be exercised headless: in a DummyOutput render the
    screen->line translation clamps every click to the last visible line, which is
    exactly why the click is bound to the row's OWN fragment handler instead.
    """
    import wth_ai.cli.ui.picker as picker_mod

    state = ListPickerState(items=[f"model-{i:03d}" for i in range(10)])
    result: dict = {"value": None}
    app = _FakeApp()
    monkeypatch.setattr(picker_mod, "get_app", lambda: app, raising=False)

    handler = picker_mod.item_click_handler(state, result, 2)
    from prompt_toolkit.mouse_events import MouseEventType

    handler(_mouse(5, kind=MouseEventType.MOUSE_DOWN))
    assert state.index == 0, "klik-turun tidak boleh memilih"
    assert result["value"] is None

    handler(_mouse(5))
    assert state.index == 2, "klik tidak memilih baris itu"
    assert result["value"] is None

    handler(_mouse(5))
    assert result["value"] == "model-002", "klik kedua tidak mengaktifkan"
    assert app.exited is True


def test_item_rows_carry_their_own_handler():
    from wth_ai.cli.ui.picker import format_rows

    state = ListPickerState(items=["a", "b", "c"])
    rows = format_rows(state, handler_for=lambda index: f"handler-{index}")
    labels = [row for row in rows if len(row) > 2]
    assert labels and all(row[2].startswith("handler-") for row in labels), rows


def test_real_click_sequence_never_cancels_the_picker():
    """A click must not close the model list.

    Measured before the fix: every click arrived as row 0, and row 0 meant "cancel",
    so clicking anywhere dismissed the picker.
    """
    ctx = _drive_picker([f"model-{i:03d}" for i in range(50)], per_item=1)
    state, pipe, result = ctx["state"], ctx["pipe"], ctx["result"]
    try:
        for row in (1, 3, 6, 10):
            pipe.send_text(f"\x1b[<0;5;{row}M\x1b[<0;5;{row}m")
            ctx["sleep"](0.2)
        assert result["value"] is None, "klik membatalkan/memilih padahal tidak boleh"
        assert ctx["app_running"](), "picker tertutup oleh klik"
    finally:
        ctx["stop"]()


def test_keyboard_navigation_still_works_alongside_the_mouse():
    import time

    ctx = _drive_picker([f"model-{i:03d}" for i in range(50)])
    state, pipe = ctx["state"], ctx["pipe"]
    try:
        pipe.send_text("\x1b[B")                   # panah bawah
        time.sleep(0.3)
        assert state.selected() == "model-001"
        pipe.send_text("\x1b[B")
        time.sleep(0.3)
        assert state.selected() == "model-002"
    finally:
        ctx["stop"]()
