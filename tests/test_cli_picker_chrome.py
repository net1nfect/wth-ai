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
