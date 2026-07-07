"""Forward platform job events to REPL trace / activity stream (verbose mode)."""

from __future__ import annotations

from typing import Any, Callable

from wth_ai.cli.ui.activity_stream import verbose_enabled
from wth_ai.events import Event, get_event_bus

_JOB_UI_TYPES = [
    "job.started",
    "job.progress",
    "job.log",
    "job.completed",
    "job.failed",
]


def _activity_mode(config: Any) -> str:
    ui = getattr(config, "ui", None) if config else None
    return getattr(ui, "activity_mode", "live") if ui else "live"


def wire_job_activity(orch: Any) -> Callable[[Event], None]:
    """Subscribe job lifecycle logs to trace panel and stream output."""

    def on_event(event: Event) -> None:
        config = getattr(orch, "config", None)
        if not verbose_enabled(config):
            return
        trace = getattr(orch, "_tool_trace", None)
        stream = getattr(orch, "_activity_stream", None)
        mode = _activity_mode(config)
        data = event.data or {}

        if event.type == "job.progress":
            current = int(data.get("current", 0))
            total = int(data.get("total", 100))
            message = str(data.get("message", ""))
            pct = int(100 * current / max(total, 1))
            label = message or f"job {event.job_id[:8]}"
            if trace and mode == "live":
                trace.set_phase(label, pct)
            if stream and mode == "stream":
                stream.on_phase(label, pct)
        elif event.type == "job.log":
            line = str(data.get("line", ""))
            if not line:
                return
            if trace and mode == "live":
                trace.add_log_line(line)
            if stream and mode == "stream":
                stream.on_log(line)
        elif event.type == "job.started":
            job_type = str(data.get("type", ""))
            if trace and mode == "live":
                trace.set_phase(job_type or "job", 0)
                trace.add_log_line(f"job {event.job_id[:8]} started")
            if stream and mode == "stream":
                stream.on_log(f"job {event.job_id[:8]} {job_type} started")
        elif event.type == "job.completed":
            if trace and mode == "live":
                trace.set_phase("idle", 100)
                trace.add_log_line(f"job {event.job_id[:8]} completed")
            if stream and mode == "stream":
                stream.on_log(f"job {event.job_id[:8]} completed")
        elif event.type == "job.failed":
            reason = str(data.get("reason", ""))
            if trace and mode == "live":
                trace.add_log_line(f"job failed: {reason[:80]}")
            if stream and mode == "stream":
                stream.on_log(f"job failed: {reason[:80]}")

    get_event_bus().subscribe(on_event, types=list(_JOB_UI_TYPES))
    return on_event


def unwire_job_activity(handler: Callable[[Event], None]) -> None:
    get_event_bus().unsubscribe(handler)
