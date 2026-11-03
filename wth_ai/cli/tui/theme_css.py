"""TUI stylesheet, generated from the shared CLI theme.

`theme.tcss` used to hardcode the GitHub dark palette (#0d1117, #161b22,
#58a6ff, #8b949e) while `cli/ui/theme.py` carried a different identity (cyan
brand, violet plan, gray-900 surfaces). The REPL and the TUI therefore looked
like two unrelated products, and changing a colour meant remembering to edit
two files -- the exact drift the shared THEME dict exists to prevent.

The CSS is now built from THEME, so one token change reaches both surfaces. A
test asserts every theme colour appears here and that the legacy palette is
gone, which makes the drift impossible to reintroduce silently.
"""

from __future__ import annotations

from wth_ai.cli.ui.theme import THEME

#: Structural styles only -- every colour comes from THEME.
_TEMPLATE = """
Screen {{
    background: {surface};
}}

HeaderBar {{
    height: 3;
    background: {surface_alt};
    border: solid {border};
    border-title-color: {brand};
    padding: 0 1;
    content-align: left middle;
}}

FooterBar {{
    height: 1;
    background: {surface_alt};
    color: {muted};
    padding: 0 1;
    dock: bottom;
}}

#transcript {{
    height: 1fr;
    border: solid {border};
    background: {surface};
    padding: 0 1;
}}

#activity-panel {{
    width: 1fr;
    height: 100%;
    border: solid {border};
    background: {surface};
    padding: 0 1;
}}

#activity-full {{
    height: 1fr;
    border: solid {border};
    background: {surface};
    padding: 0 1;
}}

#sidebar-panel {{
    width: 38;
    height: 100%;
    border: solid {border};
    background: {surface_alt};
    padding: 0 1;
}}

#input-bar {{
    height: 3;
    border: tall {border};
    background: {surface};
    padding: 0 1;
    dock: bottom;
}}

#input-bar:focus {{
    border: tall {brand};
}}

#input-bar:disabled {{
    opacity: 0.55;
    border: tall {border};
}}

#footer {{
    border-top: solid {border};
}}

#hint-line {{
    height: 1;
    color: {muted};
    padding: 0 1;
    background: {surface};
}}

.lower-row {{
    height: 12;
    min-height: 10;
    max-height: 16;
}}

TabbedContent {{
    height: 1fr;
}}

TabbedContent > ContentSwitcher {{
    height: 1fr;
}}

TabPane {{
    padding: 0;
}}

#findings-pane, #tools-pane, #config-pane {{
    height: 1fr;
    padding: 1 2;
    background: {surface};
    border: solid {border};
}}

TranscriptPanel {{
    height: 1fr;
}}

ActivityPanel {{
    height: 1fr;
}}

SidebarPanel {{
    height: 1fr;
}}

InputBar {{
    height: 3;
}}

InputBar.-disabled {{
    color: {muted};
}}

/* Tab highlights */
TabbedContent Tab {{
    background: {surface_alt};
    color: {muted};
}}

TabbedContent Tab.-active {{
    background: {border};
    color: {brand};
    text-style: bold;
}}
"""


def build_tui_css() -> str:
    """The TUI stylesheet, with every colour taken from the shared THEME."""
    keys = ("surface", "surface_alt", "border", "brand", "muted", "text",
            "accent", "warn", "error", "plan")
    values = {key: THEME[key] for key in keys}
    return _TEMPLATE.format(**values)


#: Import-time constant so `App.CSS` can reference it without a call.
TUI_CSS = build_tui_css()
