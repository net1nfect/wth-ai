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


# --------------------------------------------------------------------------- #
# palette consistency between the REPL and the TUI
#
# Measured before this: `theme.tcss` hardcoded GitHub dark (#0d1117, #161b22,
# #58a6ff, #8b949e) while `cli/ui/theme.py` carried a cyan/violet identity, so
# the REPL and the TUI looked like two different products and one colour change
# needed two edits. The CSS is now generated from THEME.
# --------------------------------------------------------------------------- #
_LEGACY_GITHUB_PALETTE = ("#0d1117", "#161b22", "#010409", "#58a6ff", "#8b949e", "#21262d",
                          "#30363d", "#484f58")


def test_tui_css_uses_the_shared_identity_colours():
    from wth_ai.cli.tui.theme_css import build_tui_css
    from wth_ai.cli.ui.theme import THEME

    css = build_tui_css()
    for key in ("surface", "surface_alt", "border", "brand", "muted"):
        assert THEME[key] in css, f"warna THEME['{key}'] tidak dipakai TUI"


def test_every_colour_in_the_tui_css_comes_from_the_theme():
    """The real anti-drift lock, and stronger than a spot check.

    Any hex literal in the stylesheet that is not a THEME value means someone
    reintroduced a hardcoded colour -- which is exactly how the TUI ended up
    looking like a different product.
    """
    import re

    from wth_ai.cli.tui.theme_css import build_tui_css
    from wth_ai.cli.ui.theme import THEME

    allowed = {value.lower() for value in THEME.values() if isinstance(value, str)}
    stray = sorted({
        match.lower() for match in re.findall(r"#[0-9a-fA-F]{6}", build_tui_css())
    } - allowed)
    assert not stray, f"warna di luar THEME: {stray}"


def test_tui_css_drops_the_legacy_github_palette():
    from wth_ai.cli.tui.theme_css import build_tui_css

    css = build_tui_css().lower()
    for legacy in _LEGACY_GITHUB_PALETTE:
        assert legacy not in css, f"palet lama {legacy} masih ada di CSS TUI"


def test_app_css_comes_from_the_shared_theme_not_a_side_file():
    from wth_ai.cli.tui.app import WthTuiApp
    from wth_ai.cli.ui.theme import THEME

    assert not getattr(WthTuiApp, "CSS_PATH", None), "TUI masih memuat stylesheet terpisah"
    assert THEME["surface"] in WthTuiApp.CSS
    assert THEME["brand"] in WthTuiApp.CSS


def _stub_orchestrator():
    from unittest.mock import MagicMock

    orch = MagicMock()
    orch.config.ui.max_trace_lines = 20
    orch.config.ui.mode = "tui"
    orch.client.available.return_value = True
    orch.state.scope.target = "example.com"
    orch.state.session_id = "abc12345"
    orch.state.findings = []
    orch.state.metadata = {}
    orch._ui_bus = None
    return orch


def test_tui_boots_headless_and_adopts_the_shared_palette():
    """The app must actually MOUNT -- none of the other TUI tests boot it."""
    pytest.importorskip("textual")
    import asyncio

    from wth_ai.cli.tui.app import WthTuiApp
    from wth_ai.cli.ui.theme import THEME

    expected = THEME["surface"].lstrip("#")
    want = tuple(int(expected[i:i + 2], 16) for i in (0, 2, 4))

    async def _boot():
        app = WthTuiApp(_stub_orchestrator())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            screen = app.screen.styles.background
            rgb = (screen.r, screen.g, screen.b) if hasattr(screen, "r") else None
            return rgb, app

    rgb, _ = asyncio.run(_boot())
    assert rgb == want, f"layar TUI {rgb} != THEME['surface'] {want}"


def test_tui_every_tab_activates():
    pytest.importorskip("textual")
    import asyncio

    from textual.widgets import TabbedContent

    from wth_ai.cli.tui.app import WthTuiApp

    async def _boot():
        app = WthTuiApp(_stub_orchestrator())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            tabs = app.query_one("#main-tabs", TabbedContent)
            seen = []
            for name in ("tab-activity", "tab-findings", "tab-tools", "tab-config", "tab-chat"):
                tabs.active = name
                await pilot.pause()
                seen.append(tabs.active)
            return seen

    assert asyncio.run(_boot()) == [
        "tab-activity", "tab-findings", "tab-tools", "tab-config", "tab-chat"
    ]


def test_tui_header_and_footer_are_populated():
    """An empty header/footer is the TUI version of dead UI."""
    pytest.importorskip("textual")
    import asyncio

    from wth_ai.cli.tui.app import WthTuiApp

    async def _boot():
        app = WthTuiApp(_stub_orchestrator())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()

            async def _text(widget_id: str) -> str:
                # Static-based widgets keep their content behind `render()`;
                # `.renderable` alone reads empty and made working chrome look
                # broken (a `or` fallback in an earlier probe hid the difference,
                # which is why both accessors are tried explicitly here).
                for _ in range(10):
                    node = app.query_one(widget_id)
                    candidates = []
                    try:
                        candidates.append(node.render())
                    except Exception:  # noqa: BLE001
                        pass
                    candidates.append(getattr(node, "renderable", None))
                    for raw in candidates:
                        if raw is None:
                            continue
                        text = getattr(raw, "plain", None) or str(raw)
                        if text and text.strip():
                            return text
                    await pilot.pause()
                return ""

            return await _text("#header"), await _text("#footer")

    header, footer = asyncio.run(_boot())
    assert "WTH-AI" in header
    assert "example.com" in header, header
    assert "ctrl+p" in footer.lower() or "palette" in footer.lower(), footer
