"""Telegram notification adapter (outbound only)."""

from __future__ import annotations

import requests

from wth_ai.integrations.base import NotificationAdapter


class TelegramNotifier(NotificationAdapter):
    name = "telegram"

    def recipients(self) -> list[str]:
        return list(getattr(self.cfg, "allowed_chat_ids", []))

    def deliver(self, recipient: str, text: str, token: str | None) -> bool:
        if not token:
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": recipient, "text": text}, timeout=15)
        return resp.ok
