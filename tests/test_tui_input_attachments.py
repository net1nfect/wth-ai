"""Regression tests for TUI input, clipboard paste, and attachments."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("textual")

from textual import events  # noqa: E402


def _app_orch():
    orch = MagicMock()
    orch.config.ui.max_trace_lines = 20
    orch.config.ui.mode = "tui"
    orch.config.providers = {
        "planner": SimpleNamespace(type="openrouter", model="model-a"),
    }
    orch.config.models = SimpleNamespace(planner="model-a")
    orch.client.available.return_value = True
    orch.client.list_models.return_value = ["model-a", "model-b", "vendor/model-c"]
    orch.client.provider_for_role.return_value = SimpleNamespace(base_url="http://localhost")
    orch.state.scope.target = "example.com"
    orch.state.session_id = "abc12345"
    orch.state.findings = []
    orch.state.metadata = {}
    orch._ui_bus = None
    orch.handle_input.return_value = "ok"
    return orch


def test_input_bar_preserves_multiline_paste():
    from wth_ai.cli.tui.app import WthTuiApp

    async def _run():
        app = WthTuiApp(_app_orch())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            bar = app.query_one("#input-bar")
            bar.focus()
            await bar._on_paste(events.Paste("first\nsecond\nthird"))
            await pilot.pause()
            return bar.text

    assert asyncio.run(_run()) == "first\nsecond\nthird"


def test_input_bar_enter_submits_and_shift_enter_newlines():
    from wth_ai.cli.tui.app import WthTuiApp

    async def _run():
        app = WthTuiApp(_app_orch())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            bar = app.query_one("#input-bar")
            bar.focus()
            bar.insert("hello")
            await pilot.pause()
            await pilot.press("shift+enter")
            await pilot.pause()
            multiline = bar.text
            await pilot.press("enter")
            await pilot.pause()
            return multiline, bar.text

    multiline, after_submit = asyncio.run(_run())
    assert multiline == "hello\n"
    assert after_submit == ""


def test_pasted_existing_path_requests_attachment(tmp_path):
    from wth_ai.cli.tui.widgets.input_bar import AttachmentRequested, InputBar

    path = tmp_path / "notes.txt"
    path.write_text("hello attachment", encoding="utf-8")
    bar = InputBar()
    seen = []
    bar.post_message = seen.append  # type: ignore[method-assign]

    async def _run():
        await bar._on_paste(events.Paste(str(path)))

    asyncio.run(_run())
    assert len(seen) == 1
    assert isinstance(seen[0], AttachmentRequested)
    assert seen[0].paths == [str(path)]
    assert bar.text == ""


def test_ctrl_o_prefills_attach_command():
    from wth_ai.cli.tui.app import WthTuiApp

    async def _run():
        app = WthTuiApp(_app_orch())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            bar = app.query_one("#input-bar")
            return bar.text, bar.cursor_location

    text, cursor = asyncio.run(_run())
    assert text == "/attach "
    assert cursor == (0, 8)


def test_lower_activity_row_is_hidden_by_default_and_toggles():
    from wth_ai.cli.tui.app import WthTuiApp

    async def _run():
        app = WthTuiApp(_app_orch())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            row = app.query_one(".lower-row")
            before = row.display
            app.action_toggle_activity()
            await pilot.pause()
            after = row.display
            return before, after

    assert asyncio.run(_run()) == (False, True)


def test_input_and_footer_do_not_overlap():
    from wth_ai.cli.tui.app import WthTuiApp

    async def _run():
        app = WthTuiApp(_app_orch())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            bar = app.query_one("#input-bar")
            bar.insert("\n".join(str(i) for i in range(20)))
            await pilot.pause()
            return bar.region, app.query_one("#footer").region

    input_region, footer_region = asyncio.run(_run())
    assert input_region.y + input_region.height <= footer_region.y


def test_model_picker_filters_and_applies_model():
    from wth_ai.cli.tui.app import WthTuiApp
    from wth_ai.cli.tui.widgets.input_bar import Submitted

    async def _run():
        app = WthTuiApp(_app_orch())
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            await app.on_submitted(Submitted("/model"))
            await pilot.pause()
            screen = app.screen
            assert type(screen).__name__ == "ModelPickerScreen"
            screen.query_one("#model-search").value = "vendor"
            await pilot.pause()
            assert screen._filtered == ["vendor/model-c"]
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            return app.orch.handle_input.call_args_list

    calls = asyncio.run(_run())
    assert calls
    assert calls[-1].args[0] == "/model set planner vendor/model-c"


def test_attachment_content_parts_include_text_and_image(tmp_path):
    from wth_ai.agents import build_messages
    from wth_ai.attachments import attachment_from_path

    text = tmp_path / "a.txt"
    text.write_text("secret text", encoding="utf-8")
    image = tmp_path / "a.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    )
    attachments = [attachment_from_path(text).to_dict(), attachment_from_path(image).to_dict()]
    messages = build_messages("sys", [], "lihat", attachments)
    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "lihat"}
    assert any("secret text" in part.get("text", "") for part in content)
    assert any(part.get("type") == "image_url" for part in content)


def test_attach_handler_accumulates_and_clears(tmp_path):
    from wth_ai.cli.handlers.attachment_handler import handle_attach

    one = tmp_path / "one.txt"
    one.write_text("one", encoding="utf-8")
    orch = SimpleNamespace(state=SimpleNamespace(metadata={}), _persist=lambda: None)
    first = handle_attach(orch, [str(one)])
    assert "1 lampiran" in first
    assert orch.state.metadata["pending_attachments"][0]["name"] == "one.txt"
    assert handle_attach(orch, ["clear"]).startswith("Lampiran dibersihkan")
    assert orch.state.metadata["pending_attachments"] == []
