"""CLI UI component tests."""

from __future__ import annotations

import os
from io import StringIO

os.environ.setdefault("WTH_NO_ANIMATION", "1")

from rich.console import Console

from wth_ai.cli.ui.animations import animations_enabled
from wth_ai.cli.ui.renderer import render_response
from wth_ai.cli.ui.tool_trace import ToolTracePanel


def test_tool_trace_complete():
    p = ToolTracePanel()
    p.add_pending("recon_run_tool", {"tool_name": "builtin-dns"})
    p.complete("recon_run_tool", "dns=2", 42)
    assert any(e.status == "done" for e in p.entries)


def test_short_slash_response_plain():
    from wth_ai.config import WTHConfig

    buf = StringIO()
    console = Console(file=buf, width=100, highlight=False)
    render_response(console, "Engagement mode: `default`", config=WTHConfig())
    out = buf.getvalue()
    assert "Engagement mode" in out
    assert "┌" not in out


def test_renderer_error_plain():
    buf = StringIO()
    c = Console(file=buf, width=100, highlight=False)
    render_response(c, "ERROR: something failed")
    assert "error" in buf.getvalue().lower() or "ERROR" in buf.getvalue()


def test_no_animation_env():
    assert os.environ.get("WTH_NO_ANIMATION") == "1"
    assert animations_enabled() is False


def test_repl_has_slash_completer():
    """The completer must still complete slash commands.

    Asserts the CAPABILITY, not the wrapper class: the session now wraps the
    completer in `FuzzyCompleter` (so `/reocn` still finds `/recon`), and locking
    the concrete type made that improvement look like a regression.
    """
    from prompt_toolkit.completion import CompleteEvent, Completer
    from prompt_toolkit.document import Document

    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    assert isinstance(repl.session.completer, Completer)

    # A real CompleteEvent, not None: FuzzyCompleter reads it, and passing None
    # made the completer look broken when the completer was fine.
    event = CompleteEvent(completion_requested=True)
    completions = list(repl.session.completer.get_completions(Document("/rec", 4), event))
    assert completions, "tidak ada completion untuk '/rec'"
    names = {c.text for c in completions}
    assert any(n.startswith("/rec") for n in names), names


def test_fuzzy_command_still_matches_without_the_broken_wrapper():
    """`/rcn` must still find `/recon`.

    `FuzzyCompleter` looked like the obvious way to get this, and it silently
    emptied the menu (it strips the slash before delegating). The capability is
    asserted here so the wrapper is never re-added as an "improvement".
    """
    from prompt_toolkit.completion import Completer
    from prompt_toolkit.document import Document

    from wth_ai.cli.repl import WTHRepl

    repl = WTHRepl(lambda t: t)
    assert isinstance(repl.session.completer, Completer)
    completions = list(repl.session.completer.get_completions(Document("/rcn", 4), None))
    assert any("/recon" in c.text for c in completions), [c.text for c in completions]

def test_repl_single_live_handler():
    """Progress+Live nesting caused 'Only one live display' — use one Live only."""
    from io import StringIO

    from wth_ai.cli.repl import WTHRepl
    import wth_ai.cli.ui.animations as anim

    buf = StringIO()
    console = Console(file=buf, width=100, highlight=False, force_terminal=True)
    trace = ToolTracePanel()
    repl = WTHRepl(lambda t: f"echo:{t}", tool_trace=trace)
    repl.console = console
    orig = anim.animations_enabled
    anim.animations_enabled = lambda: True
    try:
        assert repl._run_handler("ping") == "echo:ping"
    finally:
        anim.animations_enabled = orig
