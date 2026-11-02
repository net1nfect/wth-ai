"""Unified terminal shell — plain unicode REPL default, Textual opt-in."""

from __future__ import annotations

from typing import Any

from rich.console import Console

from wth_ai.cli.repl import WTHRepl
from wth_ai.cli.theme import PROMPT_SYMBOL
from wth_ai.cli.tui.bus import use_tui
from wth_ai.cli.ui.context_bar import render_context_bar
from wth_ai.cli.ui.status_panel import render_status_line
from wth_ai.cli.ui.tool_trace import ToolTracePanel


def _tui_missing_message() -> str:
    return (
        "Textual TUI not installed. Install with: pip install 'wth-ai[tui]' "
        "or use plain terminal (default)."
    )


def run_terminal_shell(orchestrator: Any) -> None:
    """Run interactive shell for embedded or remote orchestrator."""
    config = getattr(orchestrator, "config", None)

    if use_tui(config):
        try:
            from wth_ai.cli.tui.app import WthTuiApp

            WthTuiApp(orchestrator=orchestrator).run()
            return
        except ImportError:
            Console(highlight=False).print(f"[yellow]{_tui_missing_message()}[/]")

    trace = ToolTracePanel(max_lines=getattr(getattr(config, "ui", None), "max_trace_lines", 20))

    def status_fn() -> str:
        ts = orchestrator.tool_registry.summary()
        status = render_status_line(
            orchestrator.state,
            config,
            tools_installed=ts["installed"],
            tools_total=ts["total"],
            llm_online=orchestrator.client.available(),
            agent_phase=getattr(orchestrator._supervisor_ctx, "current_phase", "idle"),
            tool_count=getattr(orchestrator._supervisor_ctx, "tool_count", 0),
        )
        return render_context_bar(orchestrator) + "\n" + status

    def handler(text: str) -> str:
        return orchestrator.handle_input(text)

    repl = WTHRepl(
        handler,
        banner="",
        status_fn=status_fn,
        prompt_fn=lambda: PROMPT_SYMBOL,
        tool_trace=trace,
        orchestrator=orchestrator,
        config=config,
    )
    repl.run()
