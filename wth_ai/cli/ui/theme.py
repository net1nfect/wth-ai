"""CLI theme v3 — modern masthead + live status.

Kept as a plain dict + constants so every renderer (rich console, textual TUI,
status bar, completer) reads ONE palette. Adding a colour here changes the whole
CLI; hard-coding hex values in renderers is what makes CLIs drift into looking
like five different tools.
"""

from __future__ import annotations

THEME = {
    "prompt": "#9ca3af",
    "prompt_busy": "#6b7280",
    "accent": "#4ade80",
    "muted": "#6b7280",
    "text": "#e5e7eb",
    "error": "#f87171",
    "thinking": "#a3a3a3",
    "border": "#374151",
    "recon": "#22d3ee",
    "execute": "#fbbf24",
    "verify": "#4ade80",
    "plan": "#a78bfa",
    "report": "#60a5fa",
    "warn": "#fbbf24",
    "brand": "#22d3ee",
    "brand_alt": "#a78bfa",
    "surface": "#111827",
}

#: Wordmark mark only -- the name and version are rendered as styled text next
#: to it. A two-row block-art logo was tried first and read as a rendering glitch
#: rather than a logo; a single geometric mark is unambiguous at any font and
#: does not depend on the glyphs a terminal happens to carry.
LOGO = ["◢◤"]
LOGO_NAME = "WTH·AI"

PROMPT_SYMBOL = "❯ "
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SEP = " · "
STATUS_ONLINE = "●"
STATUS_OFFLINE = "○"
BAR_FULL = "▰"
BAR_EMPTY = "▱"

#: Shown under the masthead. Keys first, because that is what a new operator
#: actually needs; slash commands are discoverable via the completion menu.
HINT_LINE = (
    "/help  ·  tab: perintah  ·  ↑ riwayat  ·  ctrl+r cari  ·  ctrl+l bersihkan  ·  ctrl+d keluar"
)

PHASE_COLORS = {
    "scope": THEME["plan"],
    "recon": THEME["recon"],
    "plan": THEME["plan"],
    "execute": THEME["execute"],
    "verify": THEME["verify"],
    "report": THEME["report"],
    "idle": THEME["muted"],
}

#: Category -> icon + colour, used by the completion menu and /help so the same
#: glyph means the same thing everywhere.
CATEGORY_STYLE: dict[str, tuple[str, str]] = {
    "meta": ("⚙", THEME["muted"]),
    "recon": ("◈", THEME["recon"]),
    "target": ("◎", THEME["accent"]),
    "model": ("✦", THEME["plan"]),
    "job": ("▶", THEME["execute"]),
    "evidence": ("▤", THEME["report"]),
    "report": ("▤", THEME["report"]),
    "safety": ("⚠", THEME["warn"]),
    "memory": ("◍", THEME["verify"]),
    "tui": ("▣", THEME["brand"]),
}


def category_icon(category: str) -> tuple[str, str]:
    """(icon, colour) for a command category, with a safe default."""
    return CATEGORY_STYLE.get((category or "").lower(), ("·", THEME["muted"]))


def budget_bar(used: int, limit: int, *, width: int = 10) -> str:
    """`▰▰▰▱▱▱▱▱▱▱ 33%` — a bar an operator can read at a glance.

    Returns "" when no limit is configured, rather than pretending 0/0 is a
    measurement.
    """
    if limit <= 0:
        return ""
    filled = min(width, int(round(width * min(used, limit) / limit)))
    return f"{BAR_FULL * filled}{BAR_EMPTY * (width - filled)} {int(100 * min(used, limit) / limit)}%"
