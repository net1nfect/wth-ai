"""WTH-AI fullscreen Textual application."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Static, TabbedContent, TabPane

from wth_ai import __version__
from wth_ai.cli.quit import SessionQuit
from wth_ai.cli.tui.bus import UiEventBus
from wth_ai.cli.tui.command_class import should_reset_activity_feed
from wth_ai.cli.tui.event_bridge import unwire_platform_events, wire_platform_events
from wth_ai.cli.tui.screens.help import SlashHelpScreen
from wth_ai.cli.tui.screens.palette import CommandPaletteScreen
from wth_ai.cli.tui.screens.skills_picker import SkillsPickerScreen
from wth_ai.cli.tui.widgets.activity import ActivityPanel
from wth_ai.cli.tui.widgets.footer import FooterBar
from wth_ai.cli.tui.widgets.header import HeaderBar
from wth_ai.cli.tui.widgets.input_bar import InputBar, Submitted
from wth_ai.cli.tui.widgets.sidebar import SidebarPanel
from wth_ai.cli.tui.widgets.transcript import TranscriptPanel
from wth_ai.cli.ui.tool_trace import ToolTracePanel
from wth_ai.cli.tui.theme_css import TUI_CSS
from wth_ai.platform.registry import get_platform_registry

_QUIT_COMMANDS = frozenset({"/quit", "/exit", "quit", "exit"})


def _is_quit_command(text: str) -> bool:
    return text.strip().lower() in _QUIT_COMMANDS


class WthTuiApp(App[None]):
    """Full interactive terminal UI — non-blocking command execution."""

    # Generated from `cli/ui/theme.py` (single source of truth) instead of a
    # separate .tcss with its own hardcoded palette: the REPL and the TUI now
    # share one identity.
    CSS = TUI_CSS
    TITLE = "WTH-AI"
    BINDINGS = [
        Binding("ctrl+1", "focus_tab_chat", "Chat", show=False),
        Binding("ctrl+2", "focus_tab_activity", "Activity", show=False),
        Binding("ctrl+3", "focus_tab_findings", "Findings", show=False),
        Binding("ctrl+4", "focus_tab_tools", "Tools", show=False),
        Binding("ctrl+5", "focus_tab_config", "Config", show=False),
        Binding("ctrl+p", "command_palette", "Palette", show=False),
        Binding("ctrl+slash", "slash_help", "Help", show=False),
        Binding("ctrl+k", "skills_picker", "Skills", show=False),
        Binding("ctrl+l", "clear_transcript", "Clear", show=False),
        Binding("ctrl+t", "toggle_activity", "Activity", show=False),
        Binding("ctrl+c", "interrupt_turn", "Interrupt", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
    ]

    def __init__(self, orchestrator: Any, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.orch = orchestrator
        self.bus = UiEventBus()
        self._stream_buf = ""
        self._show_activity = True
        self._interrupt = False
        self._busy = False
        self._platform_handler = None
        self._job_activity_handler = None
        self._spinner_timer = None
        self._chrome_timer = None
        self._user_tab = "tab-chat"

    def _tabs(self) -> TabbedContent:
        return self.query_one("#main-tabs", TabbedContent)

    def _remember_tab(self) -> None:
        try:
            self._user_tab = self._tabs().active or "tab-chat"
        except Exception:
            pass

    def _restore_tab(self) -> None:
        """Keep user's tab choice — never force-switch during or after commands."""
        try:
            tabs = self._tabs()
            if self._user_tab and self._user_tab in tabs.tab_ids:
                tabs.active = self._user_tab
        except Exception:
            pass

    def _switch_tab(self, tab_id: str) -> None:
        try:
            self._tabs().active = tab_id
            self._user_tab = tab_id
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with TabbedContent(id="main-tabs"):
            with TabPane("Chat", id="tab-chat"):
                yield TranscriptPanel(id="transcript", markup=True, wrap=True)
            with TabPane("Activity", id="tab-activity"):
                yield ActivityPanel(id="activity-full", markup=True)
            with TabPane("Findings", id="tab-findings"):
                yield Static("Loading findings…", id="findings-pane", markup=True)
            with TabPane("Tools", id="tab-tools"):
                yield Static("Loading tools…", id="tools-pane", markup=True)
            with TabPane("Config", id="tab-config"):
                yield Static("Loading config…", id="config-pane", markup=True)
        with Horizontal(classes="lower-row"):
            yield ActivityPanel(id="activity-panel", markup=True)
            yield SidebarPanel(id="sidebar-panel")
        yield Static("", id="hint-line")
        yield FooterBar(id="footer")
        yield InputBar(id="input-bar")

    def on_mount(self) -> None:
        self.bus.bind_app(self)
        self.orch._ui_bus = self.bus
        self.orch._tool_trace = ToolTracePanel(max_lines=self.orch.config.ui.max_trace_lines)
        if hasattr(self.orch, "_patch_terminal_verbose"):
            self.orch._patch_terminal_verbose()
        self._wire_bus()
        self._platform_handler = wire_platform_events(self.bus)
        from wth_ai.cli.ui.job_activity_bridge import wire_job_activity
        self._job_activity_handler = wire_job_activity(self.orch)
        self._refresh_chrome()
        transcript = self.query_one("#transcript", TranscriptPanel)
        transcript.append_system(
            "WTH-AI TUI — slash: /recon /hunt /skills · natural language for chat"
        )
        transcript.append_system("Ctrl+P palette · Ctrl+/ help · Activity updates in real-time")
        llm_ok = getattr(self.orch.client, "agentic_available", self.orch.client.available)()
        if not llm_ok:
            transcript.append_system(
                "⚠ LLM offline — `wth setup` untuk API key. Slash commands tetap jalan."
            )
        self.query_one("#input-bar", InputBar).focus()

    def _header(self) -> HeaderBar:
        return self.query_one("#header", HeaderBar)

    def _wire_bus(self) -> None:
        activity = self.query_one("#activity-panel", ActivityPanel)
        activity_full = self.query_one("#activity-full", ActivityPanel)
        header = self._header()
        transcript = self.query_one("#transcript", TranscriptPanel)

        def _both(fn_name: str, *args, **kwargs) -> None:
            getattr(activity, fn_name)(*args, **kwargs)
            getattr(activity_full, fn_name)(*args, **kwargs)

        def on_tool_start(name: str, args: dict) -> None:
            _both("tool_start", name, args)
            if self.orch._tool_trace:
                self.orch._tool_trace.add_pending(name, args)

        def on_tool_end(name: str, result: str, ms: int) -> None:
            _both("tool_end", name, result, ms)
            if self.orch._tool_trace:
                self.orch._tool_trace.complete(name, result, ms)

        def on_phase(phase: str, pct: int) -> None:
            _both("set_phase", phase, pct)
            header.set_progress(phase, pct)

        def on_token(text: str) -> None:
            self._stream_buf += text
            transcript.append_token(text)

        def on_response(markdown: str) -> None:
            if markdown:
                transcript.append_response(markdown)

        def on_user(text: str) -> None:
            transcript.append_user(text)

        def on_thinking(label: str = "working") -> None:
            _both("thinking", label)
            header.set_progress(label, header._pct)

        def on_subagent_start(agent_id: str, prompt: str = "") -> None:
            _both("subagent_line", agent_id, "running", prompt)

        def on_subagent_end(agent_id: str, summary: str = "") -> None:
            _both("subagent_line", agent_id, "done", summary)

        def on_job_progress(job_id: str, current: int, total: int, message: str) -> None:
            _both("job_progress", job_id, current, total, message)
            pct = int(100 * current / max(total, 1))
            header.set_progress(message or f"job {job_id[:8]}", pct)

        def on_job_started(job_id: str, job_type: str) -> None:
            _both("job_started", job_id, job_type)
            header.set_progress(job_type or "job", 0)

        def on_job_completed(job_id: str) -> None:
            _both("job_completed", job_id)
            header.set_progress("idle", 100)
            self._refresh_chrome()

        def on_job_failed(job_id: str, reason: str) -> None:
            _both("job_failed", job_id, reason)
            header.set_progress("failed", 0)

        def on_recon_completed(**data: object) -> None:
            _both("recon_completed", **data)
            self._refresh_chrome()

        def on_llm_start(role: str, provider: str, model: str, meta: dict | None = None) -> None:
            _both("llm_start", role, provider, model, meta or {})

        def on_llm_tick(role: str, elapsed_s: int) -> None:
            _both("llm_tick", role, elapsed_s)

        def on_llm_end(role: str, model: str, ms: int, meta: dict | None = None) -> None:
            _both("llm_end", role, model, ms, meta or {})

        def on_subprocess_line(line: str) -> None:
            _both("subprocess_line", line)

        def on_activity_log(msg: str) -> None:
            _both("activity_log", msg)

        for event, handler in [
            ("tool_start", on_tool_start),
            ("tool_end", on_tool_end),
            ("phase", on_phase),
            ("token", on_token),
            ("response", on_response),
            ("user", on_user),
            ("thinking", on_thinking),
            ("subagent_start", on_subagent_start),
            ("subagent_end", on_subagent_end),
            ("job_progress", on_job_progress),
            ("job_started", on_job_started),
            ("job_completed", on_job_completed),
            ("job_failed", on_job_failed),
            ("recon_completed", on_recon_completed),
            ("llm_start", on_llm_start),
            ("llm_tick", on_llm_tick),
            ("llm_end", on_llm_end),
            ("subprocess_line", on_subprocess_line),
            ("activity_log", on_activity_log),
        ]:
            self.bus.on(event, handler)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        header = self._header()
        header.set_busy(busy)
        input_bar = self.query_one("#input-bar", InputBar)
        # Keep input enabled so focus/tabs stay usable; block submit in on_submitted.
        input_bar.disabled = False
        if busy:
            input_bar.placeholder = "⏳ running — ctrl+c interrupt · ctrl+1..5 switch tabs"
        else:
            input_bar.placeholder = "› /recon example.com  or  natural language… (Alt+Enter newline)"
        footer = self.query_one("#footer", FooterBar)
        if busy:
            footer.update(" ⏳ running · ctrl+1..5 tabs · ctrl+c interrupt · activity updates live ")
            if self._spinner_timer is None:
                self._spinner_timer = self.set_interval(0.1, self._tick_spinner)
            if self._chrome_timer is None:
                self._chrome_timer = self.set_interval(2.0, self._refresh_chrome)
        else:
            footer.on_mount()
            if self._spinner_timer is not None:
                self._spinner_timer.stop()
                self._spinner_timer = None
            if self._chrome_timer is not None:
                self._chrome_timer.stop()
                self._chrome_timer = None
            self._restore_tab()
            input_bar.focus()

    def _tick_spinner(self) -> None:
        if self._busy:
            self._header().tick_spinner()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Remember tab the user picked — never override while busy."""
        self._user_tab = event.tab.id or self._user_tab

    def _execute_command(self, text: str) -> str:
        if hasattr(self.orch, "_patch_terminal_verbose"):
            self.orch._patch_terminal_verbose()
        if self.orch.config.ui.stream_tokens:
            def stream_cb(t: str) -> None:
                if not self._interrupt:
                    self.bus.emit("token", text=t)
            self.orch.set_stream_callback(stream_cb)
        try:
            return self.orch.handle_input(text) or ""
        finally:
            self.orch.set_stream_callback(None)

    def _stop_running_work(self) -> None:
        if hasattr(self.orch, "supervisor") and hasattr(self.orch.supervisor, "stop"):
            try:
                self.orch.supervisor.stop()
            except Exception:
                pass

    def _exit_application(self, message: str = "") -> None:
        """Leave Textual UI and return to the shell."""
        self._busy = False
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._chrome_timer is not None:
            self._chrome_timer.stop()
            self._chrome_timer = None
        if message:
            try:
                self.bus.emit("response", markdown=message)
            except Exception:
                pass
        self.orch._ui_bus = None
        self.exit()

    def action_quit(self) -> None:
        """Ctrl+D — shutdown session and exit TUI."""
        if self._busy:
            self._interrupt = True
            self._stop_running_work()
        try:
            self.orch.request_quit()
        except SessionQuit as exc:
            self._exit_application(exc.message)

    def _refresh_chrome(self) -> None:
        from wth_ai.engagement.mode import get_engagement_mode
        ts = self.orch.tool_registry.summary()
        ctx = getattr(self.orch, "_supervisor_ctx", None)
        phase = getattr(ctx, "current_phase", "idle") if ctx else "idle"
        mode_label = get_engagement_mode().value.replace("_", "-")
        llm_ok = getattr(self.orch.client, "agentic_available", self.orch.client.available)()
        header = self._header()
        if not self._busy:
            header.update_status(
                version=__version__,
                target=self.orch.state.scope.target or "unset",
                perm=self.orch.state.permission_mode.value,
                llm="online" if llm_ok else "offline",
                phase=str(phase),
                mode=mode_label,
            )
        skills = self.orch.state.metadata.get("active_skills", [])
        cov = self.orch.asset_graph.coverage()
        self.query_one("#sidebar-panel", SidebarPanel).update_context(
            skills=skills,
            tools_installed=ts["installed"],
            tools_total=ts["total"],
            queue=len(self.orch.state.task_queue),
            graph_nodes=int(cov.get("nodes", 0)),
            graph_coverage=float(cov.get("coverage_pct", 0.0)),
            graph_breakdown=self.orch.asset_graph.breakdown(),
            busy=self._busy,
            phase=str(phase),
        )
        self._refresh_findings_tab()
        self._refresh_tools_tab(ts)
        self._refresh_config_tab(mode_label, llm_ok)
        if self._busy:
            self._restore_tab()

    def _refresh_findings_tab(self) -> None:
        findings = self.orch.state.findings or []
        hyps = self.orch.state.hypotheses or []
        last_job = self.orch.state.metadata.get("last_recon", {}).get("job_id", "-")
        lines = [
            "[bold #58a6ff]Findings[/]",
            f"Total: {len(findings)} · Hypotheses: {len(hyps)}",
            f"Last recon: {last_job}",
            "",
        ]
        for i, f in enumerate(findings[:12], 1):
            title = f.get("title", f.get("type", "finding")) if isinstance(f, dict) else str(f)
            sev = f.get("severity", "?") if isinstance(f, dict) else ""
            lines.append(f"  {i}. [{sev}] {str(title)[:70]}")
        if not findings:
            lines.append("[dim]No findings yet — run /hunt or /recon[/]")
        lines.append("\n[dim]/findings · /report for full output[/]")
        self.query_one("#findings-pane", Static).update("\n".join(lines))

    def _refresh_tools_tab(self, ts: dict) -> None:
        reg = self.orch.tool_registry
        lines = [
            "[bold #58a6ff]Tool Registry[/]",
            f"Installed: {ts.get('installed', 0)}/{ts.get('total', 0)}",
            "",
        ]
        try:
            available = reg.installed_tools()[:8]
            for name in available:
                lines.append(f"  [green]✓[/] {name}")
            missing = reg.missing_tools()[:5]
            if missing:
                lines.append("")
                lines.append("[dim]Missing (sample):[/]")
                for name in missing:
                    lines.append(f"  [red]✗[/] {name}")
        except Exception:
            lines.append("[dim]Use /tools list in chat[/]")
        lines.append("\n[dim]/tools list · /tools install · /doctor[/]")
        self.query_one("#tools-pane", Static).update("\n".join(lines))

    def _refresh_config_tab(self, mode_label: str, llm_ok: bool) -> None:
        ui = self.orch.config.ui
        scope = self.orch.state.scope
        lines = [
            "[bold #58a6ff]Session Config[/]",
            f"Target: [bold]{scope.target or 'unset'}[/]",
            f"Permission: {self.orch.state.permission_mode.value}",
            f"Engagement: {mode_label}",
            f"LLM: [{'green' if llm_ok else 'red'}]{'online' if llm_ok else 'offline'}[/]",
            "",
            "[bold]UI[/]",
            f"  mode: {getattr(ui, 'mode', 'plain')}",
            f"  activity: {getattr(ui, 'activity_mode', 'live')}",
            f"  verbose: {getattr(ui, 'verbose', True)}",
            f"  stream_tokens: {getattr(ui, 'stream_tokens', True)}",
            "",
            "[dim]/config show · /ui verbose on · /target example.com[/]",
        ]
        self.query_one("#config-pane", Static).update("\n".join(lines))

    async def on_submitted(self, message: Submitted) -> None:
        text = message.value
        if _is_quit_command(text):
            self.bus.emit("user", text=text)
            if self._busy:
                self._interrupt = True
                self._stop_running_work()
            try:
                await asyncio.to_thread(self.orch.handle_input, text.strip())
            except SessionQuit as exc:
                self._exit_application(exc.message)
            return

        if self._busy:
            return
        self._remember_tab()
        self.bus.emit("user", text=text)
        self._stream_buf = ""
        self._interrupt = False
        self._set_busy(True)
        self.bus.emit("thinking", label="working")
        if self.orch._tool_trace:
            self.orch._tool_trace.clear_log_lines()
        if should_reset_activity_feed(text):
            self.query_one("#activity-panel", ActivityPanel).clear_feed()
            self.query_one("#activity-full", ActivityPanel).clear_feed()
        self._restore_tab()

        try:
            response = await asyncio.to_thread(self._execute_command, text)
            if self._interrupt:
                response = (response or "") + "\n\n(interrupted)"
        except SessionQuit as exc:
            self._exit_application(exc.message)
            return
        except Exception as exc:
            response = f"error: {exc}"
        finally:
            self._set_busy(False)

        if response and not self._stream_buf:
            self.bus.emit("response", markdown=response)
        elif response and self._stream_buf and len(response) > len(self._stream_buf) + 20:
            self.bus.emit("response", markdown=response)
        elif not response:
            self.bus.emit("response", markdown="")
        self._update_hint(text)
        self._refresh_chrome()
        self._restore_tab()

    def action_focus_tab_chat(self) -> None:
        self._switch_tab("tab-chat")

    def action_focus_tab_activity(self) -> None:
        self._switch_tab("tab-activity")

    def action_focus_tab_findings(self) -> None:
        self._switch_tab("tab-findings")

    def action_focus_tab_tools(self) -> None:
        self._switch_tab("tab-tools")

    def action_focus_tab_config(self) -> None:
        self._switch_tab("tab-config")

    def _update_hint(self, text: str) -> None:
        hint = self.query_one("#hint-line", Static)
        if text.startswith("/"):
            partial = text.split()[0]
            matches = get_platform_registry().complete(partial)
            if matches:
                cmd = get_platform_registry().search(partial.lstrip("/"), limit=1)
                usage = cmd[0].usage if cmd else ""
                hint.update(f" [dim]hint:[/] {usage}")
                return
        hint.update("")

    def action_command_palette(self) -> None:
        def done(cmd: str | None) -> None:
            if cmd:
                self.query_one("#input-bar", InputBar).value = cmd if cmd.startswith("/") else f"/{cmd}"

        self.push_screen(CommandPaletteScreen(), done)

    def action_slash_help(self) -> None:
        self.push_screen(SlashHelpScreen())

    def action_skills_picker(self) -> None:
        def done(skill_id: str | None) -> None:
            if skill_id:
                self.query_one("#input-bar", InputBar).value = f"/skill {skill_id}"

        self.push_screen(SkillsPickerScreen(self.orch), done)

    def action_clear_transcript(self) -> None:
        self.query_one("#transcript", TranscriptPanel).clear()

    def action_toggle_activity(self) -> None:
        self._show_activity = not self._show_activity
        row = self.query_one(".lower-row")
        row.display = self._show_activity

    def action_interrupt_turn(self) -> None:
        if not self._busy:
            return
        self._interrupt = True
        self.bus.emit("activity_log", msg="interrupt requested")
        if hasattr(self.orch, "supervisor") and hasattr(self.orch.supervisor, "stop"):
            try:
                self.orch.supervisor.stop()
            except Exception:
                pass

    def on_unmount(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
        if self._chrome_timer is not None:
            self._chrome_timer.stop()
        if self._job_activity_handler is not None:
            from wth_ai.cli.ui.job_activity_bridge import unwire_job_activity
            unwire_job_activity(self._job_activity_handler)
            self._job_activity_handler = None
        if self._platform_handler is not None:
            unwire_platform_events(self._platform_handler)
            self._platform_handler = None
