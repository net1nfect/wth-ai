"""Full interactive Terminal UI for WTH-AI.

GAP-012: `WthTuiApp` diimpor malas (PEP 562). Sebelumnya `__init__` langsung
mengimpor `app` yang membutuhkan `textual` (optional extra `wth-ai[tui]`),
sehingga `import wth_ai.cli` — dan karenanya entry point dasar — gagal di
instalasi tanpa extra tersebut.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wth_ai.cli.tui.bus import UiEventBus, use_tui

if TYPE_CHECKING:  # pragma: no cover - hanya untuk type checker
    from wth_ai.cli.tui.app import WthTuiApp

__all__ = ["WthTuiApp", "UiEventBus", "use_tui"]


def __getattr__(name: str) -> Any:
    if name == "WthTuiApp":
        from wth_ai.cli.tui.app import WthTuiApp

        return WthTuiApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
