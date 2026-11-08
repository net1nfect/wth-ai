"""P2-5: one palette, one kill key, one progress bar.

`theme_css` already refused hex literals; the widgets and modal screens escaped
that lock, which is how GitHub-dark `#3fb950`/`#58a6ff` outlived the CSS that
was converted to THEME. This file is the gate PLAN.md names.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from wth_ai.cli.ui.theme import THEME

_TUI = pathlib.Path(__file__).resolve().parents[1] / "wth_ai" / "cli" / "tui"

_SURFACES = (
    "app.py",
    "widgets/header.py",
    "widgets/sidebar.py",
    "widgets/activity.py",
    "widgets/footer.py",
    "widgets/input_bar.py",
    "widgets/transcript.py",
    "widgets/slash_completer.py",
    "screens/help.py",
    "screens/palette.py",
    "screens/skills_picker.py",
    "redteam/agent_view.py",
    "redteam/app.py",
    "redteam/banner.py",
    "redteam/mcp_view.py",
    "redteam/skills_view.py",
    "redteam/theme_css.py",
)


def test_no_hex_literal_outside_the_theme():
    allowed = {v.lower() for v in THEME.values() if isinstance(v, str)}
    stray: dict[str, list[str]] = {}
    for rel in _SURFACES:
        path = _TUI / rel
        if not path.exists():
            continue
        hits = sorted(set(re.findall(r"#[0-9a-fA-F]{6}", path.read_text())) - allowed)
        if hits:
            stray[rel] = hits
    assert not stray, f"hex di luar THEME: {stray}"


def test_modes_palette_comes_from_the_theme():
    """`modes.py` colours are rendered by the redteam TUI, so they belong to the
    same palette. The module deliberately does not import the CLI layer, so the
    lock is on the values rather than on a THEME lookup."""
    from wth_ai.modes import MODES

    allowed = {v for v in THEME.values() if isinstance(v, str)}
    stray = sorted({m.color for m in MODES} - allowed)
    assert not stray, f"warna mode di luar THEME: {stray}"


def _bindings(cls) -> dict[str, str]:
    return {b.key: b.action for b in cls.BINDINGS}


def test_the_kill_key_does_not_also_mean_skills():
    """`ctrl+k` opened the skills picker in the main TUI and fired the kill
    switch in the redteam TUI. One key, two meanings, and the second one stops
    the engagement."""
    pytest.importorskip("textual")
    from wth_ai.cli.tui.app import WthTuiApp
    from wth_ai.cli.tui.redteam.app import WthApp

    main, red = _bindings(WthTuiApp), _bindings(WthApp)
    assert red["ctrl+k"] == "kill"
    assert main.get("ctrl+k") != "skills_picker"
    assert "skills_picker" in main.values(), "skills belum punya tombol di TUI utama"


def test_header_bar_uses_the_shared_glyphs():
    """The header bar drew `█`/`░` while `theme.budget_bar` drew `▰`/`▱` — two
    bar alphabets in one product."""
    pytest.importorskip("textual")
    from wth_ai.cli.tui.widgets.header import _bar
    from wth_ai.cli.ui.theme import BAR_EMPTY, BAR_FULL

    bar = _bar(50)
    assert BAR_FULL in bar and BAR_EMPTY in bar
    assert "█" not in bar and "░" not in bar
