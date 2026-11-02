"""Discord notification adapter (outbound only)."""

from __future__ import annotations

import requests

from wth_ai.integrations.base import NotificationAdapter


class DiscordNotifier(NotificationAdapter):
    name = "discord"

    def recipients(self) -> list[str]:
        return list(getattr(self.cfg, "allowed_channel_ids", []))

    def deliver(self, recipient: str, text: str, token: str | None) -> bool:
        if not token:
            return False
        url = f"https://discord.com/api/v10/channels/{recipient}/messages"
        headers = {"Authorization": f"Bot {token}"}
        resp = requests.post(url, headers=headers, json={"content": text}, timeout=15)
        return resp.ok
