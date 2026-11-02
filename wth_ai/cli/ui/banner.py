"""Startup masthead.

Was a three-line grey box with no context: it said the version and nothing an
operator needs to know before typing a command (which model, which target, what
permission mode, is the provider up, how many tools are installed). Now it is a
coloured wordmark plus a context row and a live-status line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.text import Text

from wth_ai import __version__
from wth_ai.cli.ui.renderer import compact_enabled
from wth_ai.cli.ui.theme import LOGO, SEP, STATUS_OFFLINE, STATUS_ONLINE, THEME, category_icon

if TYPE_CHECKING:
    from wth_ai.config import WTHConfig


def _wordmark(console: Console | None = None) -> Text:
    """`◢◤ WTH·AI  v1.0.0` — mark, name, version. No ASCII art."""
    from wth_ai.cli.ui.theme import LOGO_NAME

    out = Text()
    out.append(LOGO[0] + " ", style=f"bold {THEME['brand']}")
    out.append(LOGO_NAME, style=f"bold {THEME['brand_alt']}")
    out.append(f"  v{__version__}", style=THEME["muted"])
    return out


def _rule(console: Console | None, width: int = 64) -> Text:
    size = min(width, max(24, (console.width if console is not None else width) - 2))
    return Text("─" * size, style=THEME["border"])


def _context_row(*, config: WTHConfig | None, extra: dict[str, Any] | None = None) -> Text:
    """One line answering: model? target? permission? provider up? tools?"""
    extra = extra or {}
    row = Text()

    def _add(label: str, value: str, colour: str) -> None:
        if row.plain:
            row.append(SEP, style=THEME["border"])
        row.append(f"{label} ", style=THEME["muted"])
        row.append(value, style=colour)

    models = getattr(config, "models", None) if config is not None else None
    planner = getattr(models, "planner", "") if models is not None else ""
    if planner:
        _add("model", str(planner).split("/")[-1][:28], THEME["plan"])

    providers = getattr(config, "providers", None) if config is not None else None
    ptype = ""
    if isinstance(providers, dict) and "planner" in providers:
        ptype = str(getattr(providers["planner"], "type", "") or "")
    if ptype:
        _add("provider", ptype, THEME["accent"])

    if extra.get("target"):
        _add("target", str(extra["target"])[:40], THEME["recon"])
    if extra.get("permission"):
        _add("perm", str(extra["permission"]), THEME["warn"])
    if extra.get("profile"):
        _add("profile", str(extra["profile"]), THEME["text"])
    if extra.get("tools_total"):
        # Labelled "recon": the agent tool registry (215 tools the LLM can call)
        # is a DIFFERENT set from the recon tool registry (346). Two counts both
        # called "tools" on one screen is not an inconsistency to explain away,
        # it is an ambiguity to remove.
        _add("recon", f"{extra.get('tools_installed', 0)}/{extra['tools_total']}", THEME["muted"])
    return row


def _status_line(*, config: WTHConfig | None, extra: dict[str, Any]) -> Text:
    line = Text()
    online = extra.get("llm_online")
    if online is not None:
        dot = STATUS_ONLINE if online else STATUS_OFFLINE
        line.append(dot + " ", style=THEME["accent"] if online else THEME["error"])
        line.append("provider siap" if online else "provider belum siap", style=THEME["muted"])
    if extra.get("usage"):
        if line.plain:
            line.append(SEP, style=THEME["border"])
        line.append(str(extra["usage"]), style=THEME["muted"])
    if extra.get("results_dir"):
        if line.plain:
            line.append(SEP, style=THEME["border"])
        line.append("hasil ", style=THEME["muted"])
        line.append(str(extra["results_dir"]), style=THEME["text"])
    return line


def render_banner(
    console: Console | None = None,
    *,
    config: WTHConfig | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Print the masthead. `extra` carries live facts (target, tools, provider)."""
    c = console or Console(highlight=False)
    ui = getattr(config, "ui", None) if config is not None else None
    if ui is not None and not getattr(ui, "banner", True):
        return
    extra = extra or {}

    tagline = f"wth-ai v{__version__} · agentic CLI untuk security research"
    if compact_enabled(config):
        # Non-tty / plain mode: no boxes, no colour, one line -- logs and pipes
        # stay machine-readable.
        c.print(f"WTH-AI v{__version__} — authorized targets only")
        ctx = _context_row(config=config, extra=extra)
        if ctx.plain:
            c.print(ctx.plain)
        return

    c.print(_wordmark(c))
    c.print(Text(tagline, style=THEME["muted"]))
    c.print(_rule(c))
    c.print()
    row = _context_row(config=config, extra=extra)
    if row.plain:
        c.print(row)
    status = _status_line(config=config, extra=extra)
    if status.plain:
        c.print(status)
    c.print(Text("authorized targets only", style=THEME["warn"]))
    c.print()


def render_help_hint(console: Console | None = None) -> None:
    """Key hints, kept separate so the TUI can reuse it."""
    from wth_ai.cli.ui.theme import HINT_LINE

    c = console or Console(highlight=False)
    c.print(Text(HINT_LINE, style=THEME["muted"]))
