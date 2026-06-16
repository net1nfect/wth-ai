"""Rate-limited third-party API client."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class APIClient:
    """Perform rate-limited JSON GET requests."""

    def __init__(self, requests_per_second: float = 2.0) -> None:
        self._delay = 1.0 / requests_per_second
        self._lock = asyncio.Lock()

    async def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch JSON from a configured endpoint."""
        async with self._lock:
            await asyncio.sleep(self._delay)
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)
