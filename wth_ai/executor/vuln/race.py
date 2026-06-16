"""Race condition executor — concurrent request burst."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask


class RaceExecutor(BaseVulnExecutor):
    vuln_type = "race"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        n = 20

        def one() -> dict[str, Any]:
            try:
                r = requests.get(url, timeout=10)
                return {"status": r.status_code, "len": len(r.text)}
            except Exception as exc:
                return {"status": 0, "error": str(exc)}

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(one) for _ in range(n)]
            for f in as_completed(futures):
                results.append(f.result())

        statuses = [r.get("status") for r in results]
        success_count = sum(1 for s in statuses if s and 200 <= s < 300)
        evidence: list[str] = []
        if success_count > 1:
            evidence.append(f"{success_count}/{n} concurrent requests returned 2xx — possible race")

        return self._result(
            task, status="success",
            summary=f"Race burst {n} requests to {url[:80]}",
            evidence=evidence,
            observations=[f"2xx_count={success_count}", f"statuses_sample={statuses[:5]}"],
            risk="high" if success_count > 1 else "low",
        )
