"""Tests for the CLI look: masthead, live toolbar, completion menu.

The defect these lock down: `compact_enabled()` returned True unconditionally, so
the coloured masthead, context row and live toolbar were unreachable for every
operator -- the modern UI existed as dead code. Each test below asserts a visible
behaviour, not the presence of a function.
"""

from __future__ import annotations

import os
from io import StringIO

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from rich.console import Console

from wth_ai.cli.repl import WTHRepl
from wth_ai.cli.ui.banner import render_banner
from wth_ai.cli.ui.renderer import compact_enabled
from wth_ai.cli.ui.theme import HINT_LINE, budget_bar, category_icon


def _render(fn, **kwargs) -> str:
    buf = StringIO()
    console = Console(file=buf, width=100, highlight=False, force_terminal=True, emoji=False)
    fn(console, **kwargs)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# compact is a MODE, not the default
# --------------------------------------------------------------------------- #
def test_compact_follows_the_terminal_not_a_hardcoded_true(monkeypatch):
    """Piped output stays plain; a real terminal gets the full look."""
    monkeypatch.delenv("WTH_COMPACT", raising=False)

    class _Tty:
        def isatty(self) -> bool:
            return True

    class _Pipe:
        def isatty(self) -> bool:
            return False

    import sys

    monkeypatch.setattr(sys, "stdout", _Tty())
    assert compact_enabled(None) is False, "terminal sungguhan tidak boleh dipaksa compact"
    monkeypatch.setattr(sys, "stdout", _Pipe())
    assert compact_enabled(None) is True, "output yang dipipe harus tetap polos"


def test_wth_compact_env_overrides_both_ways(monkeypatch):
    class _Tty:
        def isatty(self) -> bool:
            return True

    import sys

    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.setenv("WTH_COMPACT", "1")
    assert compact_enabled(None) is True
    monkeypatch.setenv("WTH_COMPACT", "0")
    assert compact_enabled(None) is False


def test_explicit_config_choice_beats_autodetection(monkeypatch):
    import sys

    class _Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.delenv("WTH_COMPACT", raising=False)

    class _Cfg:
        class ui:  # noqa: N801
            compact = True

    assert compact_enabled(_Cfg()) is True


# --------------------------------------------------------------------------- #
# masthead carries context
# --------------------------------------------------------------------------- #
def test_banner_shows_logo_and_context(monkeypatch):
    # Explicit override: under pytest, stdout is not a tty, so compact mode is
    # the CORRECT behaviour there (see the autodetection test above).
    monkeypatch.setenv("WTH_COMPACT", "0")
    out = _render(
        render_banner,
        config=None,
        extra={
            "target": "example.com",
            "permission": "standard",
            "tools_total": 346,
            "tools_installed": 83,
            "llm_online": True,
        },
    )
    assert "◢◤" in out and "WTH·AI" in out, "wordmark tidak tampil"
    assert "example.com" in out, "target tidak tampil di masthead"
    assert "standard" in out
    assert "83/346" in out
    assert "provider siap" in out
    assert "authorized targets only" in out


def test_banner_degrades_to_one_line_when_compact(monkeypatch):
    monkeypatch.setenv("WTH_COMPACT", "1")
    out = _render(render_banner, config=None, extra={"target": "example.com"})
    assert "◢◤" not in out, "mode compact masih mencetak wordmark"
    assert "example.com" in out, "konteks harus tetap ada walau ringkas"


def test_banner_respects_ui_banner_false(monkeypatch):
    monkeypatch.delenv("WTH_COMPACT", raising=False)

    class _Cfg:
        class ui:  # noqa: N801
            banner = False
            compact = False

    assert _render(render_banner, config=_Cfg(), extra={}).strip() == ""


# --------------------------------------------------------------------------- #
# budget bar
# --------------------------------------------------------------------------- #
def test_budget_bar_reads_at_a_glance():
    assert budget_bar(4200, 10000) == "▰▰▰▰▱▱▱▱▱▱ 42%"
    assert budget_bar(0, 10000).endswith("0%")
    assert budget_bar(10000, 10000).endswith("100%")


def test_budget_bar_is_empty_without_a_limit():
    """0/0 is not a measurement -- render nothing instead of a fake 0%."""
    assert budget_bar(0, 0) == ""
    assert budget_bar(500, 0) == ""


# --------------------------------------------------------------------------- #
# colour helpers
# --------------------------------------------------------------------------- #
def test_category_icon_has_a_safe_default():
    icon, colour = category_icon("recon")
    assert icon and colour.startswith("#")
    unknown_icon, unknown_colour = category_icon("tidak-ada-kategori-ini")
    assert unknown_icon and unknown_colour


def test_hint_mentions_the_keys_an_operator_needs():
    for token in ("/help", "tab", "ctrl+d"):
        assert token in HINT_LINE


# --------------------------------------------------------------------------- #
# live toolbar
# --------------------------------------------------------------------------- #
def test_toolbar_renders_without_an_orchestrator():
    repl = WTHRepl(lambda t: t)
    segments = repl._toolbar()
    assert segments and all(isinstance(s, tuple) for s in segments)
    text = "".join(seg for _, seg in segments)
    # Three parts now: the closing RULE of the input line, the status, the help.
    lines = text.split("\n")
    assert len(lines) >= 3, text
    assert lines[0].startswith("─"), "baris pertama harus garis penutup input"
    status = lines[1]
    # No orchestrator: the bar still shows a working context (`~`) and controls,
    # and must not start with a stray separator.
    assert "~" in status and not status.startswith(" ·")
    assert "for agents" in lines[2]


def test_toolbar_never_raises_even_if_the_orchestrator_is_broken():
    class _Boom:
        @property
        def state(self):
            raise RuntimeError("boom")

    repl = WTHRepl(lambda t: t, orchestrator=_Boom())
    segments = repl._toolbar()          # must not raise
    assert segments
    assert "".join(text for _, text in segments).strip()


def test_toolbar_caches_between_keystrokes(monkeypatch):
    """prompt_toolkit calls this on every keypress; recomputing per keystroke
    would make typing feel heavy."""
    repl = WTHRepl(lambda t: t)
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return [("class:status", "x")]

    # The cache moved with the redesign: the per-keystroke callable now builds the
    # status line, so that is what must be counted.
    monkeypatch.setattr(repl, "_status_line", _counting)
    repl._toolbar()
    repl._toolbar()
    repl._toolbar()
    assert calls["n"] == 1, "toolbar tidak di-cache"


def test_toolbar_shows_test_target_and_permission():
    class _Scope:
        target = "example.com"

    class _Perm:
        value = "standard"

    class _State:
        scope = _Scope()
        permission_mode = _Perm()

    class _Orch:
        state = _State()

    repl = WTHRepl(lambda t: t, orchestrator=_Orch())
    text = "".join(seg for _, seg in repl._toolbar())
    assert "example.com" in text
    assert "standard" in text


# --------------------------------------------------------------------------- #
# completion menu carries descriptions
# --------------------------------------------------------------------------- #
def test_completion_entries_explain_themselves():
    from prompt_toolkit.document import Document

    from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter

    completer = SlashCompleter()
    entries = list(completer.get_completions(Document("/rec", 4), None))
    assert entries, "tidak ada completion untuk /rec"
    assert any("recon" in e.text.lower() for e in entries)
    for entry in entries:
        meta = str(entry.display_meta_text or "")
        assert meta.strip(), f"entri {entry.text!r} tidak punya deskripsi"


def test_non_slash_input_offers_nothing():
    from prompt_toolkit.document import Document

    from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter

    assert list(SlashCompleter().get_completions(Document("halo", 4), None)) == []


def test_command_argument_completion_for_tools_show():
    from prompt_toolkit.document import Document

    from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter

    entries = list(SlashCompleter().get_completions(Document("/tools show sub", 15), None))
    assert entries, "argumen tool tidak di-complete"
    assert any(e.text.startswith("sub") for e in entries)
