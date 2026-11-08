"""Adaptive tables: rich on a terminal, aligned plain text through a pipe.

Every list command printed `click.echo` lines, so `wth-ai usage` / the 200+ tool
registry / model list read as undifferentiated text and gave no scannable
structure -- while the REPL next to them was fully themed.

The rule mirrors `compact_enabled()`: a pipe, `WTH_COMPACT=1` or `--plain` gets
plain text with NO escape codes (so `wth-ai usage | grep`, logs and CI stay
machine-readable), and a real terminal gets a rich table using the shared THEME.
"""

from __future__ import annotations

import os
import shutil
from io import StringIO
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.table import Table

from wth_ai.cli.ui.renderer import compact_enabled
from wth_ai.cli.ui.theme import THEME

#: Columns wider than this are truncated in the PLAIN path only; the rich path
#: lets rich wrap. Keeps `| grep` output from being one 400-char line.
MAX_PLAIN_WIDTH = 42


def plain_output(config: Any | None = None) -> bool:
    """True when output must be plain: piped, `WTH_COMPACT`, or `--plain`."""
    if os.environ.get("WTH_PLAIN", "").strip() in {"1", "true", "yes"}:
        return True
    return compact_enabled(config)


def _is_numeric(value: str) -> bool:
    text = value.strip().replace(",", "").replace("$", "").replace("%", "").replace("·", "")
    if not text or text in {"-", "—"}:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def _numeric_columns(columns: Sequence[str], rows: Sequence[Sequence[str]]) -> set[int]:
    """Indexes that hold numbers in EVERY row -- decided, not guessed per row."""
    out: set[int] = set()
    for idx in range(len(columns)):
        values = [str(row[idx]) for row in rows if idx < len(row) and str(row[idx]).strip()]
        if values and all(_is_numeric(v) for v in values):
            out.add(idx)
    return out


def _plain_table(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    caption: str | None = None,
) -> str:
    """Aligned plain text. No colour, no box drawing, no escape sequences."""
    cells = [[str(c)[:MAX_PLAIN_WIDTH] for c in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in cells:
        for idx, value in enumerate(row[: len(widths)]):
            widths[idx] = max(widths[idx], len(value))
    numeric = _numeric_columns(columns, cells)

    def _fmt(row: Sequence[str]) -> str:
        parts = []
        for idx, width in enumerate(widths):
            value = row[idx] if idx < len(row) else ""
            parts.append(value.rjust(width) if idx in numeric else value.ljust(width))
        return "  ".join(parts).rstrip()

    out: list[str] = []
    if title:
        out.append(title)
        out.append("-" * min(len(title), 72))
    out.append(_fmt(list(columns)))
    out.append("  ".join("-" * w for w in widths))
    out.extend(_fmt(row) for row in cells)
    if caption:
        out.append("")
        out.extend(caption.splitlines())
    return "\n".join(out)


def render_table(
    title: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    caption: str | None = None,
    config: Any | None = None,
) -> str:
    """A table that adapts to where the output is going.

    Empty input still renders a readable statement instead of a bare header.
    """
    row_list = [[("" if cell is None else str(cell)) for cell in row] for row in rows]

    if plain_output(config):
        if not row_list:
            header = f"{title}\n" if title else ""
            return f"{header}(kosong)"
        return _plain_table(title, columns, row_list, caption=caption)

    # Floor of 80: a narrower console makes rich wrap the TITLE across lines and
    # the output reads broken even though the table itself is fine. The string is
    # echoed by click afterwards, so the real terminal width still applies.
    width = max(80, min(160, shutil.get_terminal_size((100, 24)).columns))
    buf = StringIO()
    console = Console(file=buf, width=width, highlight=False, force_terminal=True, emoji=False)
    if not row_list:
        if title:
            console.print(f"[bold {THEME['brand']}]{title}[/]")
        console.print(f"[{THEME['muted']}](kosong)[/]")
        return buf.getvalue().rstrip("\n")

    table = Table(
        title=title or None,
        title_style=f"bold {THEME['brand']}",
        header_style=f"bold {THEME['recon']}",
        border_style=THEME["border"],
        title_justify="left",
        caption=caption,
        caption_style=THEME["muted"],
        pad_edge=False,
    )
    numeric = _numeric_columns(columns, row_list)
    for idx, name in enumerate(columns):
        table.add_column(name, justify="right" if idx in numeric else "left", overflow="fold")
    for row in row_list:
        table.add_row(*row)
    console.print(table)
    return buf.getvalue().rstrip("\n")
