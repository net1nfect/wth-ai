"""CSS TUI red team, dibangun dari THEME bersama.

Kalau warna ditulis langsung di sini, ia menyimpang dari REPL dan dari test
yang menjaga identitas visual. Semua warna diambil dari THEME; hanya struktur
(tinggi, border, padding) yang literal.
"""

from __future__ import annotations

from wth_ai.cli.ui.theme import THEME

_TEMPLATE = """
Screen {{ background: {surface}; color: {text}; }}
#rt-hdr {{ height: 1; background: {surface_alt}; color: {text}; padding: 0 1; }}
#rt-body {{ height: 1fr; }}
#rt-left {{ width: 1fr; border-right: solid {border}; }}
#rt-log {{ background: {surface}; padding: 0 1; scrollbar-size-vertical: 1; }}
#rt-right {{ width: 44; background: {surface_alt}; padding: 0 1; }}
#rt-rtitle {{ height: 1; color: {brand}; text-style: bold; padding-top: 1; }}
#rt-qtitle {{ height: 1; color: {brand}; text-style: bold; padding-top: 1; }}
#rt-findings {{ height: 1fr; background: {surface_alt}; scrollbar-size-vertical: 1; }}
#rt-queue {{ height: auto; max-height: 10; color: {muted}; }}
#rt-prompt {{ dock: bottom; background: {surface_alt}; border-top: solid {border}; }}
#rt-hints {{ height: 1; background: {surface_alt}; color: {muted}; padding: 0 1; }}
"""


def redteam_css() -> str:
    return _TEMPLATE.format(
        surface=THEME["surface"],
        surface_alt=THEME["surface_alt"],
        text=THEME["text"],
        border=THEME["border"],
        brand=THEME["brand"],
        muted=THEME["muted"],
    )