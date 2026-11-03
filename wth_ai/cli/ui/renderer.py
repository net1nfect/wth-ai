"""Smart response renderer."""

from __future__ import annotations

import os
import sys
import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from wth_ai.cli.ui.theme import THEME

if TYPE_CHECKING:
    from wth_ai.config import WTHConfig

_META_FOOTER_RE = re.compile(r"\n+---\n\*model:.*\*$", re.DOTALL)


def compact_enabled(config: WTHConfig | None = None) -> bool:
    """Whether to render the plain one-line look.

    Measured defect: this returned True unconditionally (the config default AND
    the final fallback were both True), so the coloured masthead, the context row
    and the live toolbar were unreachable for every operator -- the "modern" UI
    was dead code unless someone flipped a config flag nobody knows about.

    The honest rule: compact exists for pipes, logs and CI, where escape codes
    are noise. On a real terminal the operator gets the full look. `WTH_COMPACT`
    still overrides both ways.
    """
    env = os.environ.get("WTH_COMPACT", "").strip().lower()
    if env in {"1", "true", "yes"}:
        return True
    if env in {"0", "false", "no"}:
        return False
    if not sys.stdout.isatty():
        return True
    if config is not None:
        ui = getattr(config, "ui", None)
        if ui is not None and hasattr(ui, "compact"):
            # An explicit setting wins -- auto-detection must not override a
            # deliberate choice.
            return bool(getattr(ui, "compact"))
    return False


def _plain_output(response: str) -> bool:
    """Short slash / status lines should not use bordered panels."""
    body = _META_FOOTER_RE.sub("", response).strip()
    if not body:
        return True
    if body.startswith("#") or "PLANNER_STATE" in body or body.startswith("```"):
        return compact_enabled()
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) <= 4 and len(body) < 600:
        return True
    return compact_enabled()


def _strip_meta_footer(response: str) -> tuple[str, str]:
    m = _META_FOOTER_RE.search(response)
    if not m:
        return response, ""
    return response[: m.start()].rstrip(), m.group(0).strip()


def render_response(console: Console, response: str, *, config: WTHConfig | None = None) -> None:
    if not response:
        return
    console.print()
    body, meta = _strip_meta_footer(response)
    plain = _plain_output(response)

    if plain:
        if body.startswith("#") or "PLANNER_STATE" in body or body.startswith("```"):
            console.print(Markdown(body[:20000]))
        elif body:
            if body.startswith("ERROR") or body.startswith("error:"):
                console.print(f"[{THEME['error']}]{body[:8000]}[/]")
            else:
                console.print(body[:20000])
        if meta:
            console.print(f"[{THEME['muted']}]{meta.lstrip('---').strip()}[/]")
        return

    if body.startswith("#") or "PLANNER_STATE" in body or body.startswith("```"):
        console.print(Panel(Markdown(body[:20000]), border_style=THEME["border"]))
    elif body.startswith("## Findings") or "| severity |" in body.lower():
        console.print(Panel(Markdown(body[:12000]), title="findings", border_style=THEME["verify"]))
    elif body.startswith("ERROR") or body.startswith("error:"):
        console.print(Panel(Text(body[:8000], style=THEME["error"]), title="error", border_style=THEME["error"]))
    elif body.startswith("EXECUTOR_RESULT") or body.startswith("VERIFICATION"):
        console.print(Panel(body[:12000], border_style=THEME["execute"]))
    elif body:
        console.print(Panel(Text(body[:20000]), border_style=THEME["border"]))
    if meta:
        console.print(f"[{THEME['muted']}]{meta.lstrip('---').strip()}[/]")


def render_findings_table(console: Console, rows: list[dict]) -> None:
    table = Table(title="Findings", border_style=THEME["border"])
    table.add_column("ID", style=THEME["muted"])
    table.add_column("Severity")
    table.add_column("Title")
    table.add_column("State")
    for r in rows[:20]:
        table.add_row(str(r.get("id", "")), str(r.get("severity", "")), str(r.get("title", ""))[:50], str(r.get("state", "")))
    console.print(table)
