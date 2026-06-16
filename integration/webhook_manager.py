"""Webhook notification manager."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import aiohttp

from core.logger import mask_sensitive


class WebhookManager:
    """Send redacted events to HTTPS webhooks."""

    async def notify(self, url: str, event: dict[str, Any]) -> int:
        """Post a redacted event to an HTTPS endpoint."""
        if urlparse(url).scheme != "https":
            raise ValueError("Webhook URL must use HTTPS")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=mask_sensitive(event)) as response:
                return response.status
