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

    def advance_spinner(self) -> str:
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        self._spinner_idx += 1
        return frame

    def set_llm(self, status: str) -> None:
        self.llm_status = status

    def clear_llm(self) -> None:
        self.llm_status = ""

    def set_phase(self, phase: str, pct: int) -> None:
        color = PHASE_COLORS.get(phase, THEME["thinking"])
        self.current_label = f"[{color}]{phase}[/{color}] {pct}%"

    def add_pending(self, name: str, args: dict[str, Any]) -> None:
        summary = _args_summary(args)
        self.entries.append(TraceEntry(name=name, args_summary=summary, status="pending"))
        self._trim()

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

    def clear_log_lines(self) -> None:
        self.log_lines.clear()

    def render(self) -> RenderableType:
        return self.render_compact(bordered=not _verbose_border())

    def render_compact(self, *, bordered: bool = False) -> RenderableType:
        frame = self.advance_spinner()
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
