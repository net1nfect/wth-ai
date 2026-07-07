"""Verbose terminal activity stream — coding-agent style live output."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from rich.console import Console

from wth_ai.cli.ui.theme import PHASE_COLORS, SPINNER_FRAMES, THEME

if True:
    from wth_ai.config import WTHConfig


def verbose_enabled(config: WTHConfig | None = None) -> bool:
    env = os.environ.get("WTH_VERBOSE", "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        return True
    if config is not None and hasattr(config, "ui"):
        mode = getattr(config.ui, "activity_mode", "live")
        if mode == "quiet":
            return False
        return bool(getattr(config.ui, "verbose", True))
    return True


def live_activity_enabled(config: WTHConfig | None = None) -> bool:
    if not sys.stdout.isatty():
        return False
    if not verbose_enabled(config):
        return False
    if config is None or not hasattr(config, "ui"):
        return True
    return getattr(config.ui, "activity_mode", "live") == "live"


def _args_line(args: dict[str, Any]) -> str:
    if not args:
        return ""
    try:
        s = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        s = str(args)
    return s[:120]


class ActivityStream:
    """Print agent/tool activity lines immediately to the terminal."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(highlight=False)
        self._token_line_open = False
        self._spinner_idx = 0
        self._llm_tick_at = 0
        self._did_stream_tokens = False

    def tick(self) -> str:
        """Advance spinner — call from Live refresh loop."""
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        self._spinner_idx += 1
        return frame

    def spinner_frame(self) -> str:
        return SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]

    def _println(self, text: str, *, style: str | None = None) -> None:
        if self._token_line_open:
            self.console.print()
            self._token_line_open = False
        if style:
            self.console.print(text, style=style)
        else:
            self.console.print(text)

    def on_thinking(self, label: str = "agent") -> None:
        frame = self.tick()
        self._println(f"[{THEME['thinking']}]{frame} {label}[/]")

    def on_llm_start(self, role: str, provider: str, model: str, meta: dict[str, Any] | None = None) -> None:
        meta = meta or {}
        tools_n = meta.get("tools", 0)
        msgs_n = meta.get("messages", 0)
        host = meta.get("host", "")
        host_bit = f" @ {host}" if host else ""
        self._println(
            f"[{THEME['plan']}]▸ llm {role}[/] "
            f"[{THEME['muted']}]{model}[/] "
            f"[dim]({provider}{host_bit} · {msgs_n} msgs · {tools_n} tools)[/]"
        )

    def on_llm_tick(self, role: str, elapsed_s: int) -> None:
        # Throttle to avoid spamming the terminal.
        if elapsed_s <= 0 or elapsed_s == self._llm_tick_at:
            return
        if elapsed_s < 5 and self._llm_tick_at:
            return
        if elapsed_s % 5 != 0 and elapsed_s > 5:
            return
        self._llm_tick_at = elapsed_s
        frame = self.spinner_frame()
        self._println(f"  [{THEME['thinking']}]{frame} waiting {role}… {elapsed_s}s[/]")

    def on_llm_end(self, role: str, model: str, duration_ms: int, meta: dict[str, Any] | None = None) -> None:
        self._llm_tick_at = 0
        meta = meta or {}
        err = meta.get("error", "")
        if err:
            self._println(f"  [{THEME['error']}]✗ llm {role} {duration_ms}ms — {err[:160]}[/]")
            return
        tool_calls = meta.get("tool_calls", 0)
        content_len = meta.get("content_len", 0)
        self._println(
            f"  [{THEME['accent']}]✓ llm {role}[/] "
            f"[{THEME['muted']}]{duration_ms}ms[/] "
            f"[dim]{model} · {tool_calls} tool calls · {content_len} chars[/]"
        )

    def on_phase(self, phase: str, pct: int) -> None:
        color = PHASE_COLORS.get(phase, THEME["thinking"])
        self._println(f"[{color}]◐ {phase}[/] [{THEME['muted']}]{pct}%[/]")

    def on_tool_start(self, name: str, args: dict[str, Any]) -> None:
        detail = _args_line(args)
        suffix = f" [dim]{detail}[/]" if detail else ""
        self._println(f"[{THEME['execute']}]▸ {name}[/]{suffix}")

    def on_tool_end(self, name: str, result: str, ms: int) -> None:
        ok = not (result or "").startswith("ERROR")
        icon = "✓" if ok else "✗"
        style = THEME["accent"] if ok else THEME["error"]
        snippet = (result or "").replace("\n", " ").strip()[:100]
        dur = f" [{THEME['muted']}]{ms}ms[/]" if ms else ""
        self._println(f"  [{style}]{icon} {name}[/]{dur} [{THEME['muted']}]{snippet}[/]")

    def on_token(self, text: str) -> None:
        if not text:
            return
        self.console.print(text, end="", style=THEME["text"])
        self._token_line_open = True
        self._did_stream_tokens = True

    def consume_streamed_tokens_flag(self) -> bool:
        """Return True if tokens were streamed since last consume, and reset."""
        v = self._did_stream_tokens
        self._did_stream_tokens = False
        return v

    def on_subprocess_line(self, line: str) -> None:
        line = (line or "").rstrip()
        if not line:
            return
        self._println(f"  [{THEME['muted']}]│ {line[:200]}[/]")

    def on_install(self, msg: str) -> None:
        self._println(f"[{THEME['recon']}]⚙ {msg}[/]")

    def on_log(self, msg: str) -> None:
        self._println(f"[{THEME['muted']}]· {msg}[/]")

    def finish_token_line(self) -> None:
        if self._token_line_open:
            self.console.print()
            self._token_line_open = False
