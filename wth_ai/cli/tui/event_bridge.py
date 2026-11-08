"""Bridge platform EventBus events into the TUI UiEventBus."""

from __future__ import annotations

from typing import Callable

from wth_ai.cli.tui.bus import UiEventBus
from wth_ai.events import Event, get_event_bus

_JOB_EVENTS = frozenset({
    "job.started",
    "job.progress",
    "job.log",
    "job.completed",
    "job.failed",
    "recon.completed",
    "recon.change_detected",
})


def wire_platform_events(ui_bus: UiEventBus) -> Callable[[Event], None]:
    """Subscribe to job/recon lifecycle events and forward to the TUI bus."""

    def on_event(event: Event) -> None:
        data = event.data or {}
        if event.type == "job.progress":
            ui_bus.emit(
                "job_progress",
                job_id=event.job_id,
                current=int(data.get("current", 0)),
                total=int(data.get("total", 100)),
                message=str(data.get("message", "")),
            )
        elif event.type == "job.log":
            ui_bus.emit("activity_log", msg=str(data.get("line", "")))
        elif event.type == "job.started":
            ui_bus.emit("job_started", job_id=event.job_id, job_type=str(data.get("type", "")))
        elif event.type == "job.completed":
            ui_bus.emit("job_completed", job_id=event.job_id)
        elif event.type == "job.failed":
            ui_bus.emit("job_failed", job_id=event.job_id, reason=str(data.get("reason", "")))
        elif event.type == "recon.completed":
            ui_bus.emit("recon_completed", job_id=event.job_id, **data)
        elif event.type == "recon.change_detected":
            ui_bus.emit("recon_change", job_id=event.job_id, **data)

    bus = get_event_bus()
    bus.subscribe(on_event, types=list(_JOB_EVENTS))
    return on_event


def unwire_platform_events(handler: Callable[[Event], None]) -> None:
    get_event_bus().unsubscribe(handler)
