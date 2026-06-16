"""Integration Manager — subscribes to the event bus and routes to adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.events import Event, EventBus, make_event
from wth_ai.integrations.base import NotificationAdapter
from wth_ai.integrations.discord import DiscordNotifier
from wth_ai.integrations.telegram import TelegramNotifier
from wth_ai.integrations.webhook import WebhookNotifier
from wth_ai.workspace import wth_home


def build_default_adapters(config: WTHConfig) -> list[NotificationAdapter]:
    ig = config.integrations
    return [
        TelegramNotifier(ig.telegram),
        DiscordNotifier(ig.discord),
        WebhookNotifier(ig.webhook),
    ]


class IntegrationManager:
    """Fan out events to enabled adapters; isolate adapter failures from core."""

    def __init__(
        self,
        config: WTHConfig,
        bus: EventBus | None = None,
        *,
        dry_run: bool = False,
        adapters: list[NotificationAdapter] | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.adapters = adapters if adapters is not None else build_default_adapters(config)
        self.log_path = log_path or (wth_home() / "logs" / "integration.log")
        if bus is not None:
            bus.subscribe(self.handle)

    def _log(self, message: str) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).isoformat()
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{ts} {message}\n")
        except OSError:
            pass  # logging must never break routing

    def handle(self, event: Event) -> list[dict[str, Any]]:
        """Route an event to all adapters. Returns per-adapter results."""
        results: list[dict[str, Any]] = []
        for adapter in self.adapters:
            try:
                res = adapter.send(event, dry_run=self.dry_run)
            except Exception as exc:  # noqa: BLE001 - core must survive adapter bugs
                res = {"adapter": adapter.name, "sent": 0, "error": {"type": "adapter", "message": str(exc)}}
            results.append(res)
            if res.get("error"):
                self._log(f"integration.error adapter={adapter.name} event={event.type} err={res['error']}")
            elif res.get("sent"):
                self._log(f"sent adapter={adapter.name} event={event.type} count={res['sent']}")
        return results

    def status(self) -> list[dict[str, Any]]:
        return [a.status() for a in self.adapters]

    def enabled_adapters(self) -> list[str]:
        return [a.name for a in self.adapters if a.enabled()]

    def test(self, *, dry_run: bool = True) -> list[dict[str, Any]]:
        """Send a synthetic event to enabled adapters (dry-run by default)."""
        event = make_event(
            "integration.error",  # any type; we force-send below
            source="system",
            data={"test": True, "message": "integration self-test"},
        )
        results: list[dict[str, Any]] = []
        for adapter in self.adapters:
            if not adapter.enabled():
                results.append({"adapter": adapter.name, "skipped": "disabled"})
                continue
            recipients = adapter.recipients()
            if dry_run:
                results.append({
                    "adapter": adapter.name,
                    "dry_run": True,
                    "would_send": len(recipients),
                    "token": adapter.status()["token"],
                })
            else:
                results.append(adapter.send(event, dry_run=False))
        return results
