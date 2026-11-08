#!/usr/bin/env python3
"""Regenerate the mode × capability table in `docs/MODES.md`.

`docs/MODES.md` claims it is generated from `wth_ai/modes.py`; this script makes
that true. Prose sections are preserved -- only the contiguous `|` table block
is replaced.

    python scripts/gen_modes_docs.py            # rewrite in place
    python scripts/gen_modes_docs.py --check    # exit 1 if the file drifted
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wth_ai.modes import capability_header, matrix_rows  # noqa: E402

DOC = ROOT / "docs" / "MODES.md"


def render_table() -> str:
    header = capability_header()
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(row) + " |" for row in matrix_rows()),
    ]
    return "\n".join(lines)


def replace_table(text: str, table: str) -> str:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("|")), None)
    if start is None:
        raise SystemExit(f"tidak ada tabel di {DOC}")
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return "\n".join([*lines[:start], *table.splitlines(), *lines[end:]]) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when the committed table is stale")
    parser.add_argument("--path", type=pathlib.Path, default=DOC)
    args = parser.parse_args(argv)

    current = args.path.read_text()
    expected = replace_table(current, render_table())

    if args.check:
        if current != expected:
            print(f"{args.path} sudah tidak sinkron dengan wth_ai/modes.py", file=sys.stderr)
            return 1
        return 0

    args.path.write_text(expected)
    print(f"ditulis: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
