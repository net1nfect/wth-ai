"""Agentic interactive CLI with Live tool trace."""

from __future__ import annotations

import itertools
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle
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
    # Bottom toolbar styles. prompt_toolkit only renders colours it knows about,
    # so every colour used in the toolbar has to be registered here.
    "status": THEME["muted"],
    "status-accent": THEME["accent"],
    "status-warn": THEME["warn"],
    "status-brand": THEME["brand"],
    "status-plan": THEME["plan"],
    "placeholder": THEME["muted"],
    "rule": THEME["border"],
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
        #: revision of the tool trace at the last paint (see `_refresh_live`)
        self._painted_revision: int | None = None
        self._job_activity_handler = None
        self.bindings = self._make_bindings()
        # The status used to be PRINTED after every heavy turn, so it scrolled
        # away exactly when it became useful ("am I near the budget? which model
        # answered?"). On a real terminal it now lives in a persistent bottom
        # toolbar; the printed card remains for non-tty/plain runs.
        self.live_status = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._toolbar_cache = ""
        self._toolbar_at = 0.0
        from wth_ai.cli.tui.widgets.slash_completer import build_completer

        self.session: PromptSession = PromptSession(
            history=FileHistory(history_path),
            key_bindings=self.bindings,
            style=PROMPT_STYLE,
            multiline=False,
            completer=build_completer(),
            auto_suggest=AutoSuggestFromHistory(),
            # Mouse is captured ONLY while the completion menu is open.
            #
            # Measured: capturing it permanently (mouse_support=True) makes the
            # terminal send wheel events to the app instead of scrolling its own
            # buffer, and with no menu open the app drops them -- so after a recon
            # printed a long result, the operator could no longer scroll it. With the
            # filter, the wheel is the terminal's again the moment the menu closes.
            mouse_support=Condition(self._completion_menu_open),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            reserve_space_for_menu=8,
            bottom_toolbar=self._toolbar if self.live_status else None,
            # Dim placeholder while the line is empty, so the input explains itself
            # instead of showing a bare cursor.
            placeholder=(
                FormattedText([("class:placeholder", "Kirim perintah, atau ketik / untuk command…")])
                if self.live_status else None
            ),
        )

    def _make_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-l")
        def _clear(event):
            event.app.renderer.clear()

        @kb.add("c-d")
        def _exit(event):
            event.app.exit(exception=EOFError)

        @kb.add("?")
        def _shortcuts(event):
            """`?` on an empty line opens shortcuts; typing `?` still works."""
            buf = event.app.current_buffer
            if buf.text:
                buf.insert_text("?")
                return
            from prompt_toolkit.application import run_in_terminal

            run_in_terminal(lambda: self._print_shortcuts())

        @kb.add("left")
        def _agents(event):
            """`←` on an empty line shows the agent roster; otherwise it moves the cursor."""
            buf = event.app.current_buffer
            if buf.text:
                buf.cursor_left()
                return
            from prompt_toolkit.application import run_in_terminal

            run_in_terminal(lambda: self._print_agents())

        @kb.add("c-c")
        def _cancel(event):
            """Ctrl+C clears what you typed; only an EMPTY prompt exits.

            Previously a stray Ctrl+C mid-line killed the whole session and lost
            the draft, which is the single most annoying thing about a chat CLI.
            """
            buf = event.app.current_buffer
            if buf.text:
                buf.reset()
            else:
                event.app.exit(exception=KeyboardInterrupt)

        # Ctrl+R (reverse history search) is deliberately NOT bound here: the
        # default emacs binding already does it correctly, and an earlier
        # hand-rolled version of this override broke it.

        return kb

    def _banner_extra(self) -> dict[str, Any]:
        """Live facts for the masthead: target, permission, tools, results dir."""
        extra: dict[str, Any] = {}
        state = getattr(self.orch, "state", None) if self.orch else None
        if state is not None:
            scope = getattr(state, "scope", None)
            if getattr(scope, "target", ""):
                extra["target"] = scope.target
            perm = getattr(getattr(state, "permission_mode", None), "value", "")
            if perm:
                extra["permission"] = perm
        try:
            from wth_ai.recon.registry import ReconToolRegistry

            registry = ReconToolRegistry()
            extra["tools_total"] = len(registry.tools)
            # `registry.available()` is the real availability check (PATH lookup /
            # vault key). An earlier version read a nonexistent `spec.available`
            # attribute, so the masthead confidently announced "tools 0/346"
            # while the status bar next to it said 109 available.
            extra["tools_installed"] = len(registry.available())
        except Exception:  # noqa: BLE001
            pass
        results = getattr(self.orch, "results_root", None) if self.orch else None
        if results:
            extra["results_dir"] = str(results)
        return extra

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

    def _status_line(self) -> list[tuple[str, str]]:
        """`model · target · permission · profile` — the line under the input.

        Shape follows the CLI the operator already knows (model, working context,
        access level, effort), because that line is where the eye lands before
        typing, not a place for a dashboard.
        """
        from wth_ai.cli.ui.theme import SEP

        # Every read below can fail (no orchestrator, a stub, a broken state object).
        # The status line sits under the input: if it raises, the operator loses the
        # prompt itself, which is a far worse failure than a dim status line.
        try:
            return self._status_line_inner()
        except Exception:  # noqa: BLE001
            return [("class:status", " wth-ai · (status tidak tersedia)")]

    def _status_line_inner(self) -> list[tuple[str, str]]:
        """`model · target · permission · profile` — the line under the input.

        Shape follows the CLI the operator already knows (model, working context,
        access level, effort), because that line is where the eye lands before
        typing, not a place for a dashboard.

        Parts are collected first and joined with separators BETWEEN them: an
        earlier version prefixed each segment with its own separator, which left a
        stray leading " · " whenever the first part was missing.
        """
        from wth_ai.cli.ui.theme import SEP

        parts: list[tuple[str, str]] = []          # (style, text)

        model = ""
        try:
            if self.orch is not None and hasattr(self.orch, "client"):
                model, _ = self.orch.client.model_for_role("planner")
        except Exception:  # noqa: BLE001
            model = ""
        if model:
            parts.append(("class:status-plan", model))

        state = getattr(self.orch, "state", None) if self.orch else None
        target = ""
        if state is not None:
            target = str(getattr(getattr(state, "scope", None), "target", "") or "")
        # `~` when no target is set (no project dir in an offensive CLI; the target
        # is the working context).
        parts.append(("class:status", target or "~"))

        perm = ""
        if state is not None:
            perm = getattr(getattr(state, "permission_mode", None), "value", "") or ""
        if perm:
            parts.append(("class:status-warn", perm))

        ui = getattr(self.config, "ui", None) if self.config else None
        mode = getattr(getattr(self.config, "agent", None), "mode", "") if self.config else ""
        activity = getattr(ui, "activity_mode", "") if ui else ""
        profile = "/".join(x for x in (mode, activity) if x)
        if profile:
            parts.append(("class:status-brand", profile))

        # Spend stays visible but dim: context, not headline.
        try:
            from wth_ai.llm.usage import get_ledger

            tokens = get_ledger().summary().get("total_tokens", 0)
            if tokens:
                parts.append(("class:status", f"{tokens} tok"))
        except Exception:  # noqa: BLE001
            pass

        out: list[tuple[str, str]] = []
        for index, (style, text) in enumerate(parts):
            prefix = " " if index == 0 else SEP
            out.append((style, f"{prefix}{text}"))
        return out or [("class:status-brand", " wth-ai")]

    def _rule_line(self) -> list[tuple[str, str]]:
        """A dim horizontal line bracketing the input, so the line you type on is
        visibly a LINE rather than text floating above the status bar."""
        import shutil

        width = max(20, min(200, shutil.get_terminal_size((100, 24)).columns - 1))
        return [("class:rule", "─" * width)]

    def _prompt_text(self) -> FormattedText:
        return FormattedText([
            ("class:rule", "─" * self._rule_width()),
            ("class:prompt", "\n" + (self.prompt_fn() if self.prompt_fn else PROMPT_SYMBOL)),
        ])

    def _rule_width(self) -> int:
        import shutil

        return max(20, min(200, shutil.get_terminal_size((100, 24)).columns - 1))

    def _help_line(self) -> list[tuple[str, str]]:
        return [("class:status", " ← for agents · ? for shortcuts · /help")]

    def _agents_lines(self) -> list[str]:
        """The `←` view: which agent is running which model right now."""
        from wth_ai.config import ROLE_NAMES

        lines: list[str] = []
        state = getattr(self.orch, "state", None) if self.orch else None
        phase = ""
        if state is not None:
            phase = str(getattr(state, "current_phase", "") or "")
            phase = getattr(phase, "value", phase)
        lines.append(f"fase: {phase or 'idle'}")
        lines.append("")
        for role in ROLE_NAMES:
            model = ""
            ptype = ""
            try:
                model, _ = self.orch.client.model_for_role(role)
                ptype = self.orch.client.provider_for_role(role).name
            except Exception:  # noqa: BLE001
                pass
            lines.append(f"  {role:<9} {model or '—':<28} {ptype or ''}".rstrip())
        return lines

    def _toolbar(self) -> list[tuple[str, str]]:
        """Live status for the bottom bar. Cheap, and never raises.

        Cached for a second: prompt_toolkit calls this on every keystroke, and a
        toolbar that walks the registry or the filesystem per keypress would make
        typing feel heavy. Never does network I/O -- a status bar must not block
        the prompt.
        """
        now = time.monotonic()
        if self._toolbar_cache and now - self._toolbar_at < 1.0:
            return self._toolbar_cache
        try:
            status = self._status_line()
            self._toolbar_cache = (
                self._rule_line()                       # garis bawah input
                + [("class:status", "\n")]
                + status
                + [("class:status", "\n")]
                + self._help_line()
            )
        except Exception:  # noqa: BLE001 - a broken toolbar must not break input
            self._toolbar_cache = [("class:status", "wth-ai")]
        self._toolbar_at = now
        return self._toolbar_cache

    def _build_toolbar(self) -> list[tuple[str, str]]:
        from wth_ai.cli.ui.context_bar import render_context_bar
        from wth_ai.llm.usage import get_ledger
        from wth_ai.cli.ui.theme import budget_bar

        # Stable left anchor: the bar identifies itself even when there is no
        # budget, no target and no orchestrator yet.
        out: list[tuple[str, str]] = [("class:status-brand", " wth-ai")]
        state = getattr(self.orch, "state", None) if self.orch else None
        target = ""
        if state is not None:
            scope = getattr(state, "scope", None)
            target = str(getattr(scope, "target", "") or "")
        if target:
            out.append(("class:status-warn", f" ◎ {target}"))
        perm = ""
        if state is not None:
            perm = getattr(getattr(state, "permission_mode", None), "value", "") or ""
        if perm:
            out.append(("class:status-plan", f"  {perm}"))

        if self.orch is not None and hasattr(self.orch, "client"):
            try:
                ctx = render_context_bar(self.orch)
            except Exception:  # noqa: BLE001
                ctx = ""
            if ctx:
                out.append(("class:status", f"  {ctx}"))

        # Budget + spend. `check_budget` reports against the SESSION scope, which
        # is what a running CLI can actually be held to.
        try:
            ledger = get_ledger()
            data = ledger.summary()
            tokens = data["total_tokens"]
            budget = data.get("budget", {})
            if tokens:
                out.append(("class:status", f"  {tokens} tok"))
            bar = budget_bar(data.get("session_tokens", 0), budget.get("limit_tokens", 0))
            if bar:
                style = "class:status-warn" if budget.get("over") else "class:status-accent"
                out.append((style, f"  {bar}"))
        except Exception:  # noqa: BLE001
            pass

        return out

    @staticmethod
    def _completion_menu_open() -> bool:
        """True only while the completion menu is showing.

        Used as the `mouse_support` filter so the wheel keeps scrolling the
        terminal's own output at rest, and can still click the menu when it exists.
        """
        try:
            buffer = get_app().current_buffer
        except Exception:  # noqa: BLE001 - no running app
            return False
        return bool(buffer is not None and buffer.complete_state)

    @staticmethod
    def _needs_more_input(selected: str, original: str) -> bool:
        """True when a menu choice must be finished by the operator first.

        The menu marks a choice that needs an argument with a trailing space, so the
        REPL pre-fills the prompt instead of submitting half a command. Measured
        consequence of NOT doing this: `/model use` (verb without its argument)
        persisted a model literally named "use" into the operator's config.
        """
        return selected.endswith(" ") and not original.endswith(" ")

    #: Commands whose bare form already opens an interactive view of its own.
    SELF_INTERACTIVE = frozenset({"/model", "/permission"})

    def _hand_terminal_back(self) -> None:
        """Release the terminal after a turn, whatever a nested app did.

        Report: scrolling worked, then stopped after a chat/recon ran. A nested
        picker/menu (or any app that enables mouse tracking) leaves the terminal
        sending the wheel to the application; if the release is missed for any
        reason, the operator cannot scroll anything until they restart the tool.
        Emitting the OFF sequences here is idempotent -- the terminal ignores codes
        for modes it is not in -- and it guarantees the wheel is the operator's
        again as soon as a turn ends.
        """
        import sys

        if not getattr(sys.stdout, "isatty", lambda: False)():
            return
        try:
            # disable mouse tracking (X10, button-event, any-event, SGR) + leave
            # the alternate screen if some nested app was still in it.
            sys.stdout.write("\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l\x1b[?1049l")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 - never let terminal cleanup break the REPL
            pass

    def _maybe_open_menu(self, text: str) -> str | None:
        """Open the interactive menu when the input asks for one.

        Returns the text to submit (possibly rewritten, e.g. `/config` ->
        `/config show`), or None to skip the turn (menu cancelled). Plain text and
        commands WITH arguments are returned untouched: the menu is an aid, never a
        gate between the operator and a command they already typed.
        """
        from wth_ai.cli.ui.menu import (
            build_command_menu,
            interactive_available,
            parse_subcommands,
            run_menu,
        )

        stripped = (text or "").strip()
        if not stripped.startswith("/") or not interactive_available():
            return text
        # Commands that open their OWN interactive view must not be hijacked by the
        # generic subcommand menu. Measured complaint: `/model` showed a menu of
        # list/use/set/fix/show instead of the model list the operator asked for.
        if stripped in self.SELF_INTERACTIVE:
            return text
        if stripped.endswith("?"):          # `/config?` = tanya, bukan buka menu
            return text

        tokens = stripped.split()
        if len(tokens) > 1:                  # sudah ada argumen: jangan ganggu
            return text

        root = tokens[0]
        if root == "/":
            choice = run_menu(build_command_menu(), title="Command")
            return None if choice is None else choice

        try:
            from wth_ai.platform.registry import get_platform_registry

            registry = get_platform_registry()
            command = next((c for c in registry.commands if c.name == root), None)
        except Exception:  # noqa: BLE001
            command = None
        if command is None:
            return text

        subs = parse_subcommands(getattr(command, "usage", "") or "")
        if not subs:
            return text
        from wth_ai.cli.ui.menu import MenuItem

        items = [MenuItem(label=s, description=f"{root} {s}", action=f"{root} {s}") for s in subs]
        choice = run_menu(items, title=f"{root} · {getattr(command, 'description', '')}"[:60])
        return None if choice is None else choice

    def _print_shortcuts(self) -> None:
        from wth_ai.cli.ui.picker import show_panel

        show_panel("Shortcuts", [
            "  enter          kirim perintah",
            "  /              menu command (tab melengkapi)",
            "  ↑ / ↓          riwayat perintah",
            "  ←  (kosong)    daftar agent + model aktif",
            "  ?  (kosong)    panel ini",
            "  ctrl+p         palet command (mode TUI)",
            "  ctrl+r         cari di riwayat",
            "  ctrl+c         bersihkan baris (kosong = keluar)",
            "  ctrl+d         keluar",
            "  ctrl+l         bersihkan layar",
            "",
            "  Ketik tanpa / untuk mengobrol biasa.",
        ])

    def _print_agents(self) -> None:
        from wth_ai.cli.ui.picker import show_panel

        show_panel("Agents", self._agents_lines())

    def _print_status(self) -> None:
        if self.status_fn:
            result = self.status_fn()
            if result is not None:
                if isinstance(result, str):
                    self.console.print(f"[{THEME['muted']}]{result}[/]")
                else:
                    self.console.print(result)

    def _refresh_live(self) -> None:
        """Repaint the live region ONLY when the panel actually changed.

        Measured cause of "can't scroll during/after the work": the panel repainted
        at 12fps (spinner animation) plus once per second for the elapsed ticker, and
        every repaint drags the terminal viewport back to the bottom. With the gate,
        a silent phase writes nothing at all and the terminal belongs to the operator.
        """
        if not (self._live and self.tool_trace):
            return
        revision = getattr(self.tool_trace, "revision", None)
        if revision is not None and revision == self._painted_revision:
            return
        self._painted_revision = revision
        # The spinner advances on PAINT only, so it can no longer drive repaints.
        self.tool_trace.advance_spinner()
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
            # NOTE: `auto_refresh` is left at rich's default on purpose. Turning it
            # off looked like the fix for constant repaints, but the measurement could
            # not support the claim (this run: 23 repaint markers vs 14 before -- the
            # proxy counts streamed tokens too, and answer lengths differ between
            # runs). Gating on content changes (see `_refresh_live`) IS kept: it is
            # what makes a silent phase cost zero writes.
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
            render_banner(self.console, config=self.config, extra=self._banner_extra())
        hint = HINT_LINE
        if verbose_enabled(self.config):
            hint += " · verbose on"
        self.console.print(f"[{THEME['muted']}]{hint}[/]")
        self._print_status()
        self.console.print()

        spinner = itertools.cycle(SPINNER_FRAMES)

        while True:
            try:
                user_input = self.session.prompt(self._prompt_text())
            except (EOFError, KeyboardInterrupt):
                self._shutdown_quiet()
                self.console.print(f"\n[{THEME['muted']}]bye[/]")
                break

            text = user_input.strip()
            if not text:
                continue

            selected = self._maybe_open_menu(text)
            if selected is None:
                continue
            if self._needs_more_input(selected, text):
                # Menu memilih command yang butuh argumen: isi ke prompt supaya
                # operator melihat persis apa yang akan jalan lalu menambah argumen.
                # Mengirimnya apa adanya pernah membuat '/model use' menyimpan model
                # bernama "use" ke config.
                try:
                    lanjut = self.session.prompt(self._prompt_text(), default=selected)
                except (EOFError, KeyboardInterrupt):
                    self.console.print(f"[{THEME['muted']}]dibatalkan[/]")
                    continue
                selected = lanjut
                if not selected.strip():
                    continue
            text = selected.strip()

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
            self._hand_terminal_back()
            kind = classify_command(text)
            if kind in {"heavy", "chat"} and not self.live_status:
                # On a tty the toolbar already carries this, live. Printing the
                # card as well would just push the answer off screen.
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
