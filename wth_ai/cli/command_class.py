"""Classify user input for REPL/TUI display behaviour."""

from __future__ import annotations

# Instant slash commands — no spinner/live panel, no status refresh.
_QUICK_SLASH = frozenset({
    "/help", "/status", "/sessions", "/target", "/scope", "/config", "/ui",
    "/quit", "/exit", "/doctor", "/tools", "/skills", "/skill", "/model",
    "/perm", "/permission", "/new-session", "/resume", "/gateway", "/cron",
    "/findings", "/graph", "/memory", "/setup", "/stop", "/provider", "/profile",
    "/effort",
    "/mcp",
})

# Long-running ops — show live activity and refresh status.
_HEAVY_PREFIXES = (
    "/recon", "/hunt", "/execute", "/job", "/plan", "/verify", "/report",
    "/subagent", "/delegate", "/install",
)


def classify_command(text: str) -> str:
    """Return ``chat`` | ``quick`` | ``heavy`` | ``normal``."""
    t = text.strip()
    if not t:
        return "quick"
    lower = t.lower()
    if not lower.startswith("/"):
        return "chat"
    cmd = lower.split()[0]
    if cmd in _QUICK_SLASH:
        return "quick"
    if any(lower.startswith(p) for p in _HEAVY_PREFIXES):
        return "heavy"
    return "normal"


def should_reset_activity_feed(text: str) -> bool:
    """Only clear activity panels for long-running work."""
    return classify_command(text) == "heavy"


def is_quick_command(text: str) -> bool:
    return classify_command(text) == "quick"
