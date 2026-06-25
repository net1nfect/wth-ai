"""Futuristic CLI / TUI layer (Fase 6, flowchart TUI).

Cyberpunk-neon dashboard for live recon/execution telemetry: recon status,
asset-graph viewer, risk heatmap, worker monitor, evidence stream, approval
console, and audit timeline. ``render_snapshot`` renders a one-shot Rich view
(works headless / for screenshots); ``ReconTUI`` is the live Textual app.
"""

from wth_ai.cli.tui.dashboard import DashboardData, render_snapshot

__all__ = ["DashboardData", "render_snapshot", "run_tui"]


def run_tui(*args, **kwargs):  # pragma: no cover - requires a TTY
    """Lazy import so importing the package never requires Textual."""
    from wth_ai.cli.tui.app import run_tui as _run
    return _run(*args, **kwargs)
