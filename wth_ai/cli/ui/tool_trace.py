"""Live tool trace panel for agent activity."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from wth_ai.cli.ui.theme import PHASE_COLORS, SPINNER_FRAMES, THEME


@dataclass
class TraceEntry:
    name: str
    args_summary: str
    status: str = "pending"  # pending | done | error
    summary: str = ""
    duration_ms: int = 0


@dataclass
class ToolTracePanel:
    max_lines: int = 20
    max_log_lines: int = 8
    current_label: str = "thinking"
    entries: list[TraceEntry] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    llm_status: str = ""
    _spinner_idx: int = field(default=0, repr=False)
    #: Bumped on every real change. The REPL repaints the live region only when this
    #: moves, so a silent job produces ZERO terminal writes -- measured cause of
    #: "can't scroll while/after the work": the panel used to repaint 12x/second
    #: (spinner animation + a per-second "waiting Ns…" ticker), and every repaint
    #: pins the terminal viewport to the bottom.
    revision: int = field(default=0, init=False, repr=False)

    def _touch(self) -> None:
        self.revision += 1

    def current_frame(self) -> str:
        """Current spinner frame WITHOUT advancing: rendering must be side-effect
        free, or every repaint mutates the panel and no repaint can ever be skipped."""
        return SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]

    def advance_spinner(self) -> str:
        frame = self.current_frame()
        self._spinner_idx += 1
        self._touch()
        return frame

    def set_llm(self, status: str) -> None:
        if status == self.llm_status:
            return
        self.llm_status = status
        self._touch()

    def clear_llm(self) -> None:
        if not self.llm_status:
            return
        self.llm_status = ""
        self._touch()

    def set_phase(self, phase: str, pct: int) -> None:
        color = PHASE_COLORS.get(phase, THEME["thinking"])
        label = f"[{color}]{phase}[/{color}] {pct}%"
        if label == self.current_label:
            return
        self.current_label = label
        self._touch()

    def add_pending(self, name: str, args: dict[str, Any]) -> None:
        summary = _args_summary(args)
        self.entries.append(TraceEntry(name=name, args_summary=summary, status="pending"))
        self._trim()
        self._touch()

    def complete(self, name: str, result: str, duration_ms: int) -> None:
        for e in reversed(self.entries):
            if e.name == name and e.status == "pending":
                e.status = "error" if result.startswith("ERROR") else "done"
                e.summary = result.replace("\n", " ")[:120]
                e.duration_ms = duration_ms
                return
        self.entries.append(TraceEntry(
            name=name, args_summary="", status="done", summary=result[:120], duration_ms=duration_ms,
        ))
        self._trim()
        self._touch()

    def _trim(self) -> None:
        if len(self.entries) > self.max_lines:
            self.entries = self.entries[-self.max_lines :]

    def add_log_line(self, line: str) -> None:
        text = (line or "").replace("\n", " ").strip()
        if not text:
            return
        self.log_lines.append(text[:120])
        if len(self.log_lines) > self.max_log_lines:
            self.log_lines = self.log_lines[-self.max_log_lines :]
        self._touch()

    def clear_log_lines(self) -> None:  # noqa: D401 - lihat _touch di badan
        self.log_lines.clear()
        self._touch()

    def render(self) -> RenderableType:
        return self.render_compact(bordered=not _verbose_border())

    def render_compact(self, *, bordered: bool = False) -> RenderableType:
        frame = self.current_frame()
        lines: list[RenderableType] = []
        head = self.llm_status or self.current_label
        lines.append(Text(f"{frame} {head}", style=THEME["thinking"]))
        for e in self.entries:
            icon = "●" if e.status == "pending" else ("✗" if e.status == "error" else "✓")
            style = THEME["muted"] if e.status == "pending" else THEME["accent"]
            dur = f" · {e.duration_ms}ms" if e.duration_ms else ""
            detail = e.args_summary or e.summary
            lines.append(Text(f"  {icon} {e.name}  {detail}{dur}", style=style))
        for note in self.log_lines[-self.max_log_lines :]:
            lines.append(Text(f"  │ {note}", style=THEME["muted"]))
        body = Group(*lines)
        if bordered:
            return Panel(body, title="agent activity", border_style=THEME["border"])
        return body

    def plain_log(self) -> str:
        rows = [f"- {e.name}: {e.status} {e.summary or e.args_summary}" for e in self.entries]
        return "\n".join(rows)


def should_update_elapsed(elapsed_s: int, interval: int) -> bool:
    """Whether an elapsed-seconds tick should repaint the live panel.

    First tick yes (so the operator sees "waiting"), then only every `interval`
    seconds. Measured: repainting on every tick (plus the spinner animation) pinned
    the viewport to the bottom for the whole job, which is why scrolling was
    impossible while work was in progress.
    """
    interval = max(1, int(interval or 1))
    if elapsed_s <= 1:
        return True
    return elapsed_s % interval == 0


def _verbose_border() -> bool:
    try:
        from wth_ai.cli.ui.activity_stream import verbose_enabled
        return not verbose_enabled()
    except Exception:
        return True


def _args_summary(args: dict[str, Any]) -> str:
    if not args:
        return ""
    try:
        s = json.dumps(args, ensure_ascii=False)
    except TypeError:
        s = str(args)
    return s[:80]
