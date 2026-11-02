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
    from wth_ai.cli.repl import WTHRepl
    from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter

    repl = WTHRepl(lambda t: t)
    assert isinstance(repl.session.completer, SlashCompleter)


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
