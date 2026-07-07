"""Startup banner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from wth_ai import __version__
from wth_ai.cli.ui.renderer import compact_enabled
from wth_ai.cli.ui.theme import SEP, THEME

if TYPE_CHECKING:
    from wth_ai.config import WTHConfig


def _box(lines: list[str]) -> Text:
    safe = [ln.rstrip() for ln in lines if ln.strip()]
    width = max((len(ln) for ln in safe), default=0)
    top = f"╭─{'─' * width}─╮"
    bot = f"╰─{'─' * width}─╯"
    out = [top]
    for ln in safe:
        pad = " " * (width - len(ln))
        out.append(f"│ {ln}{pad} │")
    out.append(bot)
    return Text("\n".join(out), style=THEME["muted"])


def render_banner(console: Console | None = None, *, config: WTHConfig | None = None) -> None:
    c = console or Console(highlight=False)
    ui = getattr(config, "ui", None) if config is not None else None
    if ui is not None and not getattr(ui, "banner", True):
        return

    if compact_enabled(config):
        c.print(_box([f"WTH-AI v{__version__}", "agentic CLI · chat + security research", "authorized only"]))
        c.print(f"[{THEME['muted']}]slash:{SEP}/help{SEP}/target{SEP}/model{SEP}/quit[/]")
        return

    c.print(_box([f"WTH-AI v{__version__}", "agentic CLI · chat + security research", "authorized targets only"]))
    c.print(f"[{THEME['muted']}]Use: /target · /model · /ui · /quit[/]")
