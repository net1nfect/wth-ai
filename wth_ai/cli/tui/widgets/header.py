"""Header bar widget with live progress."""

from __future__ import annotations

from textual.widgets import Static

from wth_ai.cli.ui.theme import SPINNER_FRAMES


def _bar(pct: int, width: int = 16) -> str:
    pct = max(0, min(100, pct))
    filled = int(width * pct / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:>3}%"


class HeaderBar(Static):
    """Top status header with spinner and progress."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._spin_idx = 0
        self._busy = False
        self._phase = "idle"
        self._pct = 0
        self._version = ""
        self._target = "unset"
        self._perm = ""
        self._llm = "offline"
        self._mode = "default"

    def on_mount(self) -> None:
        self._render_header()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._render_header()

    def set_progress(self, phase: str, pct: int) -> None:
        self._phase = phase or "idle"
        self._pct = max(0, min(100, int(pct)))
        self._render_header()

    def tick_spinner(self) -> None:
        self._spin_idx += 1
        self._render_header()

    def update_status(
        self,
        *,
        version: str,
        target: str,
        perm: str,
        llm: str,
        phase: str,
        mode: str = "default",
    ) -> None:
        self._version = version
        self._target = target or "unset"
        self._perm = perm
        self._llm = llm
        self._mode = mode.replace("_", "-")
        if not self._busy:
            self._phase = phase or "idle"
        self._render_header()

    def _render_header(self) -> None:
        spin = SPINNER_FRAMES[self._spin_idx % len(SPINNER_FRAMES)] if self._busy else "●"
        llm_color = "#3fb950" if self._llm == "online" else "#f85149"
        busy_tag = "[bold #d29922] RUNNING [/]" if self._busy else ""
        bar = _bar(self._pct) if self._busy or self._pct > 0 else ""
        phase_bit = f"  [dim]phase[/] [bold]{self._phase}[/]"
        bar_bit = f"  {bar}" if bar else ""
        self.update(
            f" {spin} [bold #58a6ff]WTH-AI[/] v{self._version}  {busy_tag}"
            f"[dim]·[/] {self._mode}  [dim]·[/] target: [bold]{self._target}[/]  "
            f"[dim]·[/] perm: {self._perm}  [dim]·[/] llm: [{llm_color}]{self._llm}[/]"
            f"{phase_bit}{bar_bit} "
        )
