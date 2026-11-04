"""Entry point: `python -m wth_ai.cli.tui.redteam`."""

from __future__ import annotations

import argparse

from wth_ai import __version__
from wth_ai.cli.tui.redteam.app import run
from wth_ai.modes import BY_KEY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wth-redteam",
        description="WTH-AI terminal UI — red team engine",
    )
    parser.add_argument("--demo", action="store_true",
                        help="aliran peristiwa contoh (semua baris berlabel [demo])")
    parser.add_argument("--target", default="", help="target awal, mis. 172.28.0.10")
    parser.add_argument("--cidr", default="", help="allowlist awal, mis. 172.28.0.0/16")
    parser.add_argument("--mode", default="plan", choices=sorted(BY_KEY),
                        help="mode awal (default: plan)")
    parser.add_argument("--destructive", action="store_true",
                        help="izinkan kapabilitas mutate (lab saja)")
    parser.add_argument("--version", action="version", version=f"wth-redteam {__version__}")
    args = parser.parse_args(argv)
    run(demo=args.demo, target=args.target, cidr=args.cidr, mode=args.mode,
        destructive=args.destructive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())