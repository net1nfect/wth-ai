"""Context sidebar."""

from __future__ import annotations

from textual.widgets import Static

from wth_ai.cli.ui.theme import THEME


class SidebarPanel(Static):
    def update_context(
        self,
        *,
        skills: list[str],
        tools_installed: int,
        tools_total: int,
        queue: int,
        subagents: str = "idle",
        graph_nodes: int = 0,
        graph_coverage: float = 0.0,
        graph_breakdown: dict[str, int] | None = None,
        busy: bool = False,
        phase: str = "idle",
    ) -> None:
        skill_line = ", ".join(skills[:3]) if skills else "none"
        breakdown = graph_breakdown or {}
        top_types = ", ".join(
            f"{k}:{v}" for k, v in sorted(breakdown.items(), key=lambda x: -x[1])[:4]
        )
        graph_line = f"{graph_nodes} nodes · {graph_coverage:.0f}% tested"
        if top_types:
            graph_line += f"\n  {top_types}"
        status = (
            f"[bold {THEME['warn']}]● RUNNING[/]" if busy
            else f"[{THEME['accent']}]● idle[/]"
        )
        self.update(
            f"[bold {THEME['brand']}]Context[/]\n\n"
            f"Status: {status}\n"
            f"Phase: [bold]{phase}[/]\n\n"
            f"[bold]Skills[/]\n  {skill_line}\n\n"
            f"[bold]Agent-tools[/]\n  {tools_installed}/{tools_total} installed\n\n"
            f"[bold]Queue[/]\n  {queue} tasks\n\n"
            f"[bold]Graph[/]\n  {graph_line}\n"
        )
