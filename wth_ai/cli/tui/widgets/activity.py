"""Live activity / tool trace panel — append-only for responsive updates."""

from __future__ import annotations

import json
from typing import Any

from textual.widgets import RichLog

from wth_ai.cli.ui.tool_trace import ToolTracePanel


def _args_line(args: dict[str, Any]) -> str:
    if not args:
        return ""
    try:
        s = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        s = str(args)
    return s[:100]


class ActivityPanel(RichLog):
    """Streaming activity log — never clears on each tool event."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("max_lines", 500)
        kwargs.setdefault("auto_scroll", True)
        kwargs.setdefault("highlight", True)
        super().__init__(*args, **kwargs)
        self.trace = ToolTracePanel(max_lines=40, max_log_lines=12)
        self._phase = "idle"
        self._pct = 0
        self._last_phase_write = ""

    def on_mount(self) -> None:
        self.write("[dim]Activity feed — tools, jobs, subprocess output[/]")

    def _phase_changed(self, phase: str, pct: int) -> bool:
        key = f"{phase}:{pct // 5}"
        if key == self._last_phase_write:
            return False
        self._last_phase_write = key
        return True

    def tool_start(self, name: str, args: dict) -> None:
        self.trace.add_pending(name, args)
        detail = _args_line(args)
        suffix = f" [dim]{detail}[/]" if detail else ""
        self.write(f"[#58a6ff]▸ {name}[/]{suffix}", scroll_end=True)

    def tool_end(self, name: str, result: str, ms: int) -> None:
        self.trace.complete(name, result, ms)
        ok = not (result or "").startswith("ERROR")
        icon = "✓" if ok else "✗"
        color = "#3fb950" if ok else "#f85149"
        snippet = (result or "").replace("\n", " ").strip()[:80]
        dur = f" [dim]{ms}ms[/]" if ms else ""
        self.write(f"  [{color}]{icon} {name}[/]{dur} [dim]{snippet}[/]", scroll_end=True)

    def set_phase(self, phase: str, pct: int) -> None:
        self._phase = phase
        self._pct = pct
        self.trace.set_phase(phase, pct)
        if self._phase_changed(phase, pct):
            self.write(f"[#d29922]◐ {phase}[/] [dim]{pct}%[/]", scroll_end=True)

    def job_progress(self, job_id: str, current: int, total: int, message: str) -> None:
        pct = int(100 * current / max(total, 1))
        label = message or f"job {job_id[:8]}"
        self.set_phase(label, pct)

    def job_started(self, job_id: str, job_type: str) -> None:
        self.write(f"[#58a6ff]▶ job {job_id[:8]}[/] {job_type} started", scroll_end=True)
        self.set_phase(job_type or "job", 0)

    def job_completed(self, job_id: str) -> None:
        self.write(f"[#3fb950]✓ job {job_id[:8]}[/] completed", scroll_end=True)
        self.set_phase("idle", 100)

    def job_failed(self, job_id: str, reason: str) -> None:
        self.write(f"[#f85149]✗ job {job_id[:8]}[/] {reason[:80]}", scroll_end=True)
        self.set_phase("failed", 0)

    def recon_completed(self, **data: object) -> None:
        target = str(data.get("target", ""))
        sync = data.get("graph_sync") or {}
        nodes = sync.get("nodes", 0) if isinstance(sync, dict) else 0
        line = f"[#3fb950]✓ recon[/] {target}"
        if nodes:
            line += f" · graph +{nodes} nodes"
        self.write(line, scroll_end=True)

    def subagent_line(self, agent_id: str, status: str, summary: str = "") -> None:
        self.write(f"[#d29922]◆ subagent {agent_id}[/] {status} {summary[:60]}", scroll_end=True)

    def llm_start(self, role: str, provider: str, model: str, meta: dict | None = None) -> None:
        meta = meta or {}
        tools_n = meta.get("tools", 0)
        host = meta.get("host", "")
        host_bit = f" @ {host}" if host else ""
        self.trace.set_llm(f"llm {role} · {model}")
        self.write(
            f"[#a371f7]▸ llm {role}[/] [dim]{model}[/] ({provider}{host_bit} · {tools_n} tools)",
            scroll_end=True,
        )

    def llm_tick(self, role: str, elapsed_s: int) -> None:
        self.trace.set_llm(f"llm {role} · {elapsed_s}s…")
        if elapsed_s >= 5 and elapsed_s % 5 == 0:
            self.write(f"[dim]  waiting {role}… {elapsed_s}s[/]", scroll_end=True)

    def llm_end(self, role: str, model: str, ms: int, meta: dict | None = None) -> None:
        meta = meta or {}
        err = str(meta.get("error", ""))
        self.trace.clear_llm()
        if err:
            self.write(f"[#f85149]✗ llm {role}[/] {ms}ms — {err[:80]}", scroll_end=True)
        else:
            tool_calls = meta.get("tool_calls", 0)
            self.write(
                f"[#3fb950]✓ llm {role}[/] {ms}ms · {model} · {tool_calls} tool calls",
                scroll_end=True,
            )

    def subprocess_line(self, line: str) -> None:
        text = (line or "").rstrip()
        if not text:
            return
        self.trace.add_log_line(text)
        self.write(f"[dim]│ {text[:200]}[/]", scroll_end=True)

    def activity_log(self, msg: str) -> None:
        text = (msg or "").strip()
        if not text:
            return
        self.trace.add_log_line(text)
        self.write(f"[dim]· {text[:200]}[/]", scroll_end=True)

    def thinking(self, label: str = "working") -> None:
        self.trace.set_phase(label, self._pct)
        self.write(f"[#d29922]◐ {label}…[/]", scroll_end=True)

    def clear_feed(self) -> None:
        self.clear()
        self.trace.clear_log_lines()
        self._last_phase_write = ""
        self.write("[dim]Activity feed — tools, jobs, subprocess output[/]")
