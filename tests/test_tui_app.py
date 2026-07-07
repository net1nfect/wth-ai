"""TUI fallback and import tests."""

from __future__ import annotations

import os
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

os.environ.setdefault("WTH_TEST_MODE", "1")


def test_use_tui_default_plain(monkeypatch):
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    monkeypatch.delenv("WTH_TUI", raising=False)
    from wth_ai.cli.tui.bus import use_tui
    from wth_ai.config import UIConfig, WTHConfig

    assert use_tui() is False
    cfg = WTHConfig(ui=UIConfig(mode="plain"))
    assert use_tui(cfg) is False


def test_use_tui_plain_env(monkeypatch):
    monkeypatch.setenv("WTH_PLAIN", "1")
    from wth_ai.cli.tui.bus import use_tui
    assert use_tui() is False


def test_use_tui_opt_in_env(monkeypatch):
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    monkeypatch.setenv("WTH_TUI", "1")
    from wth_ai.cli.tui.bus import use_tui
    assert use_tui() is True


def test_use_tui_config_mode(monkeypatch):
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    monkeypatch.delenv("WTH_TUI", raising=False)
    from wth_ai.cli.tui.bus import use_tui
    from wth_ai.config import UIConfig, WTHConfig

    cfg = WTHConfig(ui=UIConfig(mode="tui"))
    assert use_tui(cfg) is True


def test_use_tui_not_tty_by_default(monkeypatch):
    monkeypatch.delenv("WTH_PLAIN", raising=False)
    monkeypatch.delenv("WTH_TUI", raising=False)
    from wth_ai.cli.tui.bus import use_tui

    with patch("sys.stdout.isatty", return_value=True):
        assert use_tui() is False


def test_tui_app_importable():
    pytest.importorskip("textual")
    from wth_ai.cli.tui.app import WthTuiApp
    assert WthTuiApp is not None


def test_tui_quit_command_detection():
    from wth_ai.cli.tui.app import _is_quit_command

    assert _is_quit_command("/quit") is True
    assert _is_quit_command("/exit") is True
    assert _is_quit_command("quit") is True
    assert _is_quit_command("/help") is False


def test_tui_exit_application_calls_exit():
    pytest.importorskip("textual")
    from unittest.mock import MagicMock

    from wth_ai.cli.tui.app import WthTuiApp

    orch = MagicMock()
    app = WthTuiApp(orch)
    app.exit = MagicMock()
    app.bus = MagicMock()
    app._exit_application("WTH-AI closed")
    app.exit.assert_called_once()
    assert orch._ui_bus is None


def test_command_classify():
    from wth_ai.cli.command_class import classify_command, should_reset_activity_feed

    assert classify_command("/help") == "quick"
    assert classify_command("/status") == "quick"
    assert classify_command("/target example.com") == "quick"
    assert classify_command("/recon example.com") == "heavy"
    assert classify_command("/hunt") == "heavy"
    assert classify_command("audit this site") == "chat"
    assert should_reset_activity_feed("/help") is False
    assert should_reset_activity_feed("/recon x.com") is True
    assert should_reset_activity_feed("hello") is False


def test_ui_event_bus_thread_dispatch():
    from wth_ai.cli.tui.bus import UiEventBus

    bus = UiEventBus()
    seen: list[str] = []

    class _FakeApp:
        def call_from_thread(self, fn, event, payload):
            fn(event, payload)

    bus.bind_app(_FakeApp())
    bus.on("activity_log", lambda msg: seen.append(msg))
    bus.emit("activity_log", msg="from worker")
    assert seen == ["from worker"]


def test_header_progress_bar():
    pytest.importorskip("textual")
    from wth_ai.cli.tui.widgets.header import HeaderBar, _bar

    assert "█" in _bar(50)
    assert "50%" in _bar(50)


def test_activity_panel_append_only():
    pytest.importorskip("textual")
    from wth_ai.cli.tui.widgets.activity import ActivityPanel

    panel = ActivityPanel()
    panel.tool_start("dns_lookup", {"host": "example.com"})
    panel.tool_end("dns_lookup", "ok", 12)
    assert any(e.name == "dns_lookup" for e in panel.trace.entries)


def test_ui_event_bus():
    from wth_ai.cli.tui.bus import UiEventBus
    bus = UiEventBus()
    seen: list[str] = []
    bus.on("phase", lambda phase, pct: seen.append(phase))
    bus.emit("phase", phase="recon", pct=10)
    assert seen == ["recon"]


def test_render_status_line_unicode():
    from wth_ai.cli.ui.status_panel import render_status_line
    from wth_ai.cli.ui.theme import STATUS_ONLINE
    from wth_ai.config import WTHConfig
    from wth_ai.models import SessionState

    state = SessionState()
    state.session_id = "abcd1234-5678"
    state.scope.target = "example.com"
    line = render_status_line(state, WTHConfig(), llm_online=True, agent_phase="recon")
    assert "wth v" in line
    assert "example.com" in line
    assert STATUS_ONLINE in line
    assert "phase recon" in line
    assert "┌" not in line


def test_slash_completer_yields_help():
    from prompt_toolkit.document import Document

    from wth_ai.cli.tui.widgets.slash_completer import SlashCompleter

    comp = SlashCompleter()
    doc = Document("/he")
    names = [c.text for c in comp.get_completions(doc, None)]
    assert any("/help" in n for n in names)


def test_compact_renderer_no_panel_border(monkeypatch):
    monkeypatch.setenv("WTH_COMPACT", "1")
    from wth_ai.cli.ui.renderer import compact_enabled, render_response

    assert compact_enabled() is True
    buf = StringIO()
    console = Console(file=buf, width=100, highlight=False)
    render_response(console, "plain output line")
    out = buf.getvalue()
    assert "plain output line" in out
    assert "┌" not in out
    assert "╭" not in out


def test_doctor_check_ui():
    from wth_ai.config import WTHConfig
    from wth_ai.tools.doctor import ToolDoctor

    doc = ToolDoctor(WTHConfig())
    ui = doc.check_ui()
    assert ui["plain_repl"] is True
    assert ui["slash_router"] is True
    assert ui["ok"] is True
