"""Full interactive Terminal UI for WTH-AI.

`WthTuiApp` hanya di-export kalau textual terpasang. Sebelumnya impor eager di
sini membuat `wth-ai` (dan `wth-ai setup --check`) gagal total pada instalasi tanpa
ekstrak `tui`, padahal TUI adalah fitur opsional — pesan yang benar di situ
adalah "butuh pip install wth-ai[tui]", bukan ModuleNotFoundError di startup.
"""

from __future__ import annotations

from wth_ai.cli.tui.bus import UiEventBus, use_tui

__all__ = ["WthTuiApp", "UiEventBus", "use_tui"]


def __getattr__(name: str):
    if name == "WthTuiApp":
        from wth_ai.cli.tui.app import WthTuiApp

        return WthTuiApp
    raise AttributeError(name)
