"""Binding: tab harus priority (kalau tidak, Input yang memakannya)."""

from wth_ai.cli.tui.redteam.app import WthApp


def _bindings():
    return {b.key: b for b in WthApp.BINDINGS}


def test_tab_is_priority():
    assert _bindings()["tab"].priority is True


def test_kill_and_help_are_bound():
    keys = _bindings()
    assert "ctrl+k" in keys
    assert keys["ctrl+k"].action == "kill"
    assert "f1" in keys