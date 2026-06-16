"""Codex-minimal interactive CLI."""

from __future__ import annotations

import itertools
import sys
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.style import Style as RichStyle

from wth_ai.cli.theme import HINT_LINE, PROMPT_SYMBOL, SPINNER_FRAMES, THEME

SLASH_COMMANDS = [
    "/help", "/scope", "/scope set", "/permission", "/permission read_only",
    "/permission standard", "/permission elevated", "/permission full_lab",
    "/plan", "/recon", "/hunt", "/doctor", "/intel", "/mcp", "/tools", "/static", "/dynamic", "/hypotheses",
    "/execute", "/execute --all",
    "/findings", "/verify", "/report", "/patch", "/retest", "/stop",
    "/resume", "/sessions", "/approve", "/skills", "/skill", "/browser",
    "/chat", "/new-session", "/status",
]

PROMPT_STYLE = Style.from_dict({
    "prompt": f"bold {THEME['prompt']}",
    "completion-menu.completion": "bg:#1f2937 fg:#e5e7eb",
    "completion-menu.meta.completion": "bg:#1f2937 fg:#6b7280",
})


class WTHRepl:
    """Minimal Codex-style REPL."""

    def __init__(
        self,
        handler: Callable[[str], str],
        *,
        history_path: str = "data/cli_history",
        banner: str = "",
        status_fn: Callable[[], str] | None = None,
        prompt_fn: Callable[[], str] | None = None,
    ) -> None:
        self.handler = handler
        self.banner = banner
        self.status_fn = status_fn
        self.prompt_fn = prompt_fn
        self.console = Console(highlight=False)
        self.completer = WordCompleter(SLASH_COMMANDS, ignore_case=True, sentence=True)
        self.bindings = self._make_bindings()
        self.session: PromptSession = PromptSession(
            history=FileHistory(history_path),
            completer=self.completer,
            key_bindings=self.bindings,
            style=PROMPT_STYLE,
            multiline=False,
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

    def _print_status(self) -> None:
        if self.status_fn:
            bar = self.status_fn()
            self.console.print(f"[{THEME['muted']}]{bar}[/]")

    def run(self) -> None:
        if self.banner:
            self.console.print(self.banner, markup=True)
        self.console.print(f"[{THEME['muted']}]{HINT_LINE}[/]")
        self._print_status()
        self.console.print()

        spinner = itertools.cycle(SPINNER_FRAMES)

        while True:
            try:
                prompt_text = self.prompt_fn() if self.prompt_fn else PROMPT_SYMBOL
                user_input = self.session.prompt(prompt_text)
            except (EOFError, KeyboardInterrupt):
                self.console.print(f"\n[{THEME['muted']}]bye[/]")
                break

            text = user_input.strip()
            if not text:
                continue
            if text.lower() in {"exit", "quit", "/exit", "/quit"}:
                self.console.print(f"[{THEME['muted']}]bye[/]")
                break

            try:
                status = self._status_message(text)
                frame = next(spinner)
                if sys.stdout.isatty():
                    self.console.print(f"[{THEME['thinking']}]{frame} {status}[/]", end="\r")
                response = self.handler(text)
                if sys.stdout.isatty():
                    self.console.print(" " * (len(status) + 4), end="\r")
            except Exception as exc:
                self.console.print(f"[{THEME['error']}]error: {exc}[/]")
                continue

            self._render_response(response)
            self._print_status()
            self.console.print()

    def _status_message(self, text: str) -> str:
        lower = text.strip().lower()
        if lower.startswith("/hunt"):
            return "hunt loop"
        if lower.startswith("/doctor"):
            return "doctor"
        if lower.startswith("/tools"):
            return "tools"
        if lower.startswith("/plan"):
            return "planning"
        if lower.startswith("/execute"):
            return "executing"
        if lower.startswith("/verify"):
            return "verifying"
        if lower.startswith("/recon"):
            return "recon"
        if lower.startswith("/intel"):
            return "intel"
        return "thinking"

    def _render_response(self, response: str) -> None:
        if not response:
            return
        self.console.print()
        if response.startswith("#") or "PLANNER_STATE" in response or response.startswith("```"):
            self.console.print(Markdown(response[:20000]))
        elif response.startswith("EXECUTOR_RESULT") or response.startswith("VERIFICATION"):
            self.console.print(response[:12000], style=RichStyle(color=THEME["accent"]))
        else:
            self.console.print(response[:20000])
