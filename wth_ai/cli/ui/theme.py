"""CLI theme v2."""

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
}

PROMPT_SYMBOL = "› "
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
HINT_LINE = "slash: /help /target /model /quit · verbose activity · ctrl+l clear"
SEP = " · "
STATUS_ONLINE = "●"
STATUS_OFFLINE = "○"

PHASE_COLORS = {
    "scope": THEME["plan"],
    "recon": THEME["recon"],
    "plan": THEME["plan"],
    "execute": THEME["execute"],
    "verify": THEME["verify"],
    "report": THEME["report"],
    "idle": THEME["muted"],
}
