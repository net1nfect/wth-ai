"""Generic helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def retry(
    operation: Callable[[], Awaitable[T]], attempts: int = 3, base_delay: float = 0.25
) -> T:
    """Retry an async operation with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(base_delay * (2**attempt))
    raise RuntimeError(f"Operation failed after {attempts} attempts: {last_error}")


def deduplicate(items: list[Any]) -> list[Any]:
    """Deduplicate hashable values while preserving order."""
    return list(dict.fromkeys(items))
