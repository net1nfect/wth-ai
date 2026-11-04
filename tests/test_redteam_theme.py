"""Tema TUI red team dibangun dari THEME bersama, bukan hex lepas."""

from wth_ai.cli.tui.redteam.theme_css import redteam_css
from wth_ai.cli.ui.theme import THEME


def test_css_uses_shared_theme_colours():
    css = redteam_css()
    assert THEME["surface"] in css
    assert THEME["brand"] in css
    assert THEME["border"] in css


def test_css_targets_the_widgets_we_use():
    css = redteam_css()
    for selector in ("#rt-hdr", "#rt-log", "#rt-findings", "#rt-prompt", "#rt-hints"):
        assert selector in css