"""TTY-aware animation helpers."""

from __future__ import annotations

import itertools
import os
import sys
import time
from typing import Iterator

from wth_ai.cli.ui.theme import SPINNER_FRAMES


def animations_enabled() -> bool:
    if os.environ.get("WTH_NO_ANIMATION") == "1":
        return False
    return sys.stdout.isatty()


def spinner_frames() -> Iterator[str]:
    return itertools.cycle(SPINNER_FRAMES)


def typewriter_print(console, text: str, *, delay: float = 0.012) -> None:
    if not animations_enabled():
        console.print(text)
        return
    for ch in text:
        console.print(ch, end="")
        time.sleep(delay)
    console.print()
