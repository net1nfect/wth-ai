"""Ops dashboard: adaptive campaign telemetry + proactive daemon state."""

from __future__ import annotations

import time
from typing import Any

from textual.widgets import Static

from wth_ai.cli.ui.theme import THEME


class OpsPanel(Static):
    """Live operator view for adaptive and proactive subsystems."""

    def update_from(self, orch: Any) -> None:
        adaptive = (getattr(orch.state, "metadata", {}) or {}).get("adaptive_last") or {}
        try:
            from wth_ai.continuous.state import read_state

            auto = read_state()
        except Exception:  # noqa: BLE001
            auto = None
        try:
            from wth_ai.engagement.mode import safety_status

            safety = safety_status(getattr(orch, "config", None))
        except Exception:  # noqa: BLE001
            safety = {}

        lines: list[str] = []
        badge = (
            f"[bold {THEME['error']}]NO-LIMITS[/]"
            if safety.get("no_limits")
            else f"[{THEME['muted']}]guarded[/]"
        )
        lines.append(f"[bold {THEME['brand']}]Ops Dashboard[/]  {badge}")
        lines.append(
            f"[{THEME['muted']}]authz_wajib={not safety.get('require_authorized', True)} · "
            f"auto_approve={safety.get('auto_approve', False)} · "
            f"unrestricted={safety.get('unrestricted', False)}[/]"
        )
        lines.append("")
        lines.append(f"[bold]Adaptive campaign[/]")
        if adaptive:
            target = adaptive.get("target", "-")
            baseline = adaptive.get("baseline_status", 0)
            signal = adaptive.get("baseline_signal", "-")
            vendor = adaptive.get("vendor", "")
            lines.append(
                f"  target: [bold]{target}[/] · baseline: {baseline or '-'} {signal}"
                f"{' · ' + vendor if vendor else ''}"
            )
            lines.append(
                f"  rounds: {adaptive.get('rounds', 0)} · attempts: {len(adaptive.get('attempts') or [])} · "
                f"bypass: {'[green]YES[/]' if adaptive.get('bypassed') else '[yellow]no[/]'}"
                f" · best: {adaptive.get('best_strategy') or '-'}"
            )
            lines.append(f"  stopped: {adaptive.get('stopped_reason', '-')}")
            for attempt in (adaptive.get("attempts") or [])[-5:]:
                mark = "[green]✓[/]" if attempt.get("improved") else "[dim]·[/]"
                lines.append(
                    f"  {mark} r{attempt.get('round', 1)} {attempt.get('strategy', '')} → "
                    f"{attempt.get('status', 0) or '-'} {attempt.get('signal', '')}"
                )
        else:
            lines.append("  [dim]belum ada campaign — jalankan /adaptive <url> atau /autopilot[/]")

        lines.append("")
        lines.append("[bold]Proactive daemon[/]")
        if auto is not None:
            state_word = (
                f"[green]RUNNING[/] pid={auto.pid}" if auto.running else "[dim]idle[/]"
            )
            lines.append(f"  status: {state_word} · interval: {auto.interval}s · steps: {auto.steps}")
            lines.append(f"  targets: {', '.join(auto.targets) if auto.targets else '(none)'}")
            lines.append(
                f"  cycles: {auto.cycles} · steps: {auto.steps_run} · "
                f"adaptive: {auto.adaptive_steps} ({auto.adaptive_wins} win)"
            )
            if auto.last_cycle:
                lines.append(f"  last cycle: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(auto.last_cycle))}")
            if auto.last_error:
                lines.append(f"  [red]error:[/] {auto.last_error[:200]}")
        else:
            lines.append("  [dim]state tidak tersedia[/]")
        lines.append("")
        lines.append(
            f"[{THEME['muted']}]/adaptive <url> · /autopilot · /autonomy start|stop|status|run · "
            "ctrl+1..6 tabs[/]"
        )
        self.update("\n".join(lines))
