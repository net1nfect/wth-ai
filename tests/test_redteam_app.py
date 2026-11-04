"""TUI red team: mount, header, tabel, dan perpindahan mode dengan tab."""

import asyncio

import pytest

pytest.importorskip("textual")


def test_app_mounts_with_expected_widgets():
    from wth_ai.cli.tui.redteam.app import WthApp

    async def scenario():
        app = WthApp(demo=False, mode="plan")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.mode_key == "plan"
            assert app.query_one("#rt-hdr")
            assert len(app.query_one("#rt-findings").columns) == 4
            assert app.query_one("#rt-prompt")

    asyncio.run(scenario())


def test_tab_cycles_mode():
    from wth_ai.cli.tui.redteam.app import WthApp

    async def scenario():
        app = WthApp(demo=False, mode="plan")
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            assert app.mode_key == "build"

    asyncio.run(scenario())


def test_unknown_start_mode_falls_back_to_plan():
    from wth_ai.cli.tui.redteam.app import WthApp

    app = WthApp(mode="godmode")
    assert app.mode_key == "plan"