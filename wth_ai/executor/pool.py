"""Parallel executor task pool."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from wth_ai.models import ExecutorResult, ExecutorTask


class ExecutorPool:
    """Run multiple executor tasks concurrently with rate limiting."""

    def __init__(self, *, max_workers: int = 3, rate_limit_rps: float = 2.0) -> None:
        self.max_workers = max_workers
        self.rate_limit_rps = max(0.5, rate_limit_rps)
        self._semaphore = asyncio.Semaphore(max_workers)

    async def run_all(
        self,
        tasks: list[ExecutorTask],
        execute_fn: Callable[[ExecutorTask], ExecutorResult],
        *,
        approved_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        approved_ids = approved_ids or set()
        delay = 1.0 / self.rate_limit_rps

        async def _run_one(task: ExecutorTask) -> dict[str, Any]:
            async with self._semaphore:
                await asyncio.sleep(delay)
                result = await asyncio.to_thread(
                    execute_fn,
                    task,
                )
                return {"task_id": task.id, "result": result}

        coros = [_run_one(t) for t in tasks[: self.max_workers * 2]]
        return await asyncio.gather(*coros)

    def run_all_sync(
        self,
        tasks: list[ExecutorTask],
        execute_fn: Callable[[ExecutorTask], ExecutorResult],
        *,
        approved_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return asyncio.run(self.run_all(tasks, execute_fn, approved_ids=approved_ids))
