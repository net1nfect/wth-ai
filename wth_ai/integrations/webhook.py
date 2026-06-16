"""Generic webhook notification adapter (optional, stub-friendly)."""

from __future__ import annotations

import requests

from wth_ai.events import Event
from wth_ai.integrations.base import NotificationAdapter


class WebhookNotifier(NotificationAdapter):
    name = "webhook"

    def token(self) -> str | None:
        return None  # webhook auth (if any) lives in the URL

    def recipients(self) -> list[str]:
        url = getattr(self.cfg, "url", None)
        return [url] if url else []

    def deliver(self, recipient: str, text: str, token: str | None) -> bool:
        resp = requests.post(recipient, json={"text": text}, timeout=15)
        return resp.ok

    def send(self, event: Event, *, dry_run: bool = False) -> dict:
        # Webhook posts the full structured event rather than formatted text.
        result = super().send(event, dry_run=dry_run)
        return result

    def format_event(self, event: Event) -> str:
        import json
        return json.dumps(event.to_dict())
