"""Agentic interactive CLI with Live tool trace."""

from __future__ import annotations

import itertools
import threading
from typing import TYPE_CHECKING, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from rich.console import Console
from rich.live import Live

from wth_ai.cli.command_class import classify_command, is_quick_command
from wth_ai.cli.quit import SessionQuit
from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter
from wth_ai.cli.ui.activity_stream import ActivityStream, live_activity_enabled, verbose_enabled
from wth_ai.cli.ui.animations import animations_enabled
from wth_ai.cli.ui.banner import render_banner
from wth_ai.cli.ui.renderer import compact_enabled, render_response
from wth_ai.cli.ui.tool_trace import ToolTracePanel
from wth_ai.cli.ui.theme import HINT_LINE, PROMPT_SYMBOL, SPINNER_FRAMES, THEME

if TYPE_CHECKING:
    from wth_ai.config import WTHConfig

PROMPT_STYLE = Style.from_dict({
    "prompt": f"bold {THEME['prompt']}",
})


class WTHRepl:
    """Agentic chat REPL with animated tool trace."""

    def __init__(
        self,
        handler: Callable[[str], str],
        *,
        history_path: str = "data/cli_history",
        banner: str = "",
        status_fn: Callable[[], object] | None = None,
        prompt_fn: Callable[[], str] | None = None,
        tool_trace: ToolTracePanel | None = None,
        orchestrator: object | None = None,
        config: WTHConfig | None = None,
    ) -> None:
        self.handler = handler
        self.banner = banner
        self.status_fn = status_fn
        self.prompt_fn = prompt_fn
        self.tool_trace = tool_trace or ToolTracePanel()
        self.orch = orchestrator
        self.config = config or (getattr(orchestrator, "config", None) if orchestrator else None)
        self.console = Console(highlight=False)
        self.activity = ActivityStream(self.console)
        self._live: Live | None = None
        self._job_activity_handler = None
        self.bindings = self._make_bindings()
        self.session: PromptSession = PromptSession(
            history=FileHistory(history_path),
            key_bindings=self.bindings,
            style=PROMPT_STYLE,
            multiline=False,
            completer=SlashCompleter(),
        )

    def _make_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-l")
        def _clear(event):
            event.app.renderer.clear()

        @kb.add("c-d")
        def _exit(event):
            event.app.exit(exception=EOFError)

        return kb

    def _attach_orchestrator(self) -> None:
        if self.orch is None:
            return
        self.orch._tool_trace = self.tool_trace
        self.orch._activity_stream = self.activity
        if hasattr(self.orch, "_patch_terminal_verbose"):
            self.orch._patch_terminal_verbose()
        from wth_ai.cli.ui.job_activity_bridge import wire_job_activity
        if self._job_activity_handler is not None:
            from wth_ai.cli.ui.job_activity_bridge import unwire_job_activity
            unwire_job_activity(self._job_activity_handler)
        self._job_activity_handler = wire_job_activity(self.orch)

    def _print_status(self) -> None:
        if self.status_fn:
            result = self.status_fn()
            if result is not None:
                if isinstance(result, str):
                    self.console.print(f"[{THEME['muted']}]{result}[/]")
                else:
                    self.console.print(result)

    def _refresh_live(self) -> None:
        if self._live and self.tool_trace:
            self._live.update(self.tool_trace.render_compact(bordered=False))

    def _wire_live_callbacks(self) -> None:
        """Ensure orchestrator activity stream is attached before each turn."""
        if self.orch and hasattr(self.orch, "_patch_terminal_verbose"):
            self.orch._patch_terminal_verbose()

    def _start_live_animation(self, stop: threading.Event) -> threading.Thread:
        def _animate() -> None:
            while not stop.wait(0.08):
                self._refresh_live()

        thread = threading.Thread(target=_animate, daemon=True)
        thread.start()
        return thread

    def _show_tool_trace_plain(self) -> None:
        ui = getattr(self.config, "ui", None)
        if ui is not None and not getattr(ui, "show_tool_trace", True):
            return
        if verbose_enabled(self.config):
            return
        if not self.tool_trace or not self.tool_trace.entries:
            return
        log = self.tool_trace.plain_log()
        if log:
            self.console.print(f"[{THEME['thinking']}]◐ activity[/]")
            self.console.print(log)

    def _use_live_panel(self) -> bool:
        if not self.tool_trace:
            return False
        return live_activity_enabled(self.config)

    def _run_handler(self, text: str) -> str:
        self._wire_live_callbacks()
        if self.tool_trace:
            self.tool_trace.clear_log_lines()
        quick = is_quick_command(text)
        if not self._use_live_panel() or quick:
            if verbose_enabled(self.config) and not quick:
                self.activity.on_thinking("working")
            return self.handler(text)

        self.activity.finish_token_line()
        if self.tool_trace:
            self.tool_trace.set_llm("agent working…")
        stop = threading.Event()
        anim = self._start_live_animation(stop)
        try:
            with Live(
                self.tool_trace.render_compact(bordered=False),
                refresh_per_second=12,
                console=self.console,
                transient=False,
            ) as live:
                self._live = live
                try:
                    result = self.handler(text)
                    self.activity.finish_token_line()
                    return result
                finally:
                    self._live = None
                    if self.tool_trace:
                        self.tool_trace.clear_llm()
        finally:
            stop.set()
            anim.join(timeout=0.2)
        return ""

    def run(self) -> None:
        self._attach_orchestrator()
        if self.banner:
            self.console.print(self.banner, markup=True)
        else:
            render_banner(self.console, config=self.config)
        hint = HINT_LINE
        if verbose_enabled(self.config):
            hint += " · verbose on"
        self.console.print(f"[{THEME['muted']}]{hint}[/]")
        self._print_status()
        self.console.print()

        spinner = itertools.cycle(SPINNER_FRAMES)

        while True:
            try:
                prompt_text = self.prompt_fn() if self.prompt_fn else PROMPT_SYMBOL
                user_input = self.session.prompt(prompt_text)
            except (EOFError, KeyboardInterrupt):
                self._shutdown_quiet()
                self.console.print(f"\n[{THEME['muted']}]bye[/]")
                break

            text = user_input.strip()
            if not text:
                continue

            use_live = self._use_live_panel() and not is_quick_command(text)
            try:
                if use_live:
                    self.activity.on_thinking("agent")
                elif animations_enabled() and classify_command(text) not in {"quick"}:
                    frame = next(spinner)
                    self.console.print(f"[{THEME['thinking']}]{frame} agent[/]", end="\r")
                response = self._run_handler(text)
                if animations_enabled() and not use_live:
                    self.console.print(" " * 24, end="\r")
            except SessionQuit as quit_exc:
                self.console.print(f"[{THEME['muted']}]{quit_exc.message}[/]")
                self.console.print(f"[{THEME['muted']}]bye[/]")
                break
            except Exception as exc:
                self.console.print(f"[{THEME['error']}]error: {exc}[/]")
                continue

            ui = getattr(self.config, "ui", None)
            activity_mode = getattr(ui, "activity_mode", "live") if ui else "live"
            streamed = self.activity.consume_streamed_tokens_flag()
            if not (activity_mode == "stream" and streamed):
                render_response(self.console, response, config=self.config)
            if not use_live:
                self._show_tool_trace_plain()
            kind = classify_command(text)
            if kind in {"heavy", "chat"}:
                self._print_status()
            if kind != "quick":
                self.console.print()

    def _shutdown_quiet(self) -> None:
        if self._job_activity_handler is not None:
            from wth_ai.cli.ui.job_activity_bridge import unwire_job_activity
            unwire_job_activity(self._job_activity_handler)
            self._job_activity_handler = None
        if self.orch is not None and hasattr(self.orch, "shutdown"):
            try:
                self.orch.shutdown(reason="interrupt")
            except Exception:
                pass
