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

        # Method ditentukan dari spesifikasi task bila ada, kalau tidak: GET dulu,
        # lalu POST kalau endpoint menolak GET (404/405). Versi sebelumnya SELALU
        # GET, jadi endpoint POST seperti /redeem tidak pernah benar-benar diuji —
        # terukur: 20 request, semua 404, tidak ada temuan.
        plan_text = " ".join(str(x) for x in (task.command_plan or []))
        method = "POST" if "POST" in plan_text.upper() else "GET"

        def burst(method_name: str) -> list[dict[str, Any]]:
            def one() -> dict[str, Any]:
                try:
                    if method_name == "POST":
                        r = requests.post(url, timeout=10, data={})
                    else:
                        r = requests.get(url, timeout=10)
                    return {"status": r.status_code, "len": len(r.text), "body": r.text[:120]}
                except Exception as exc:  # noqa: BLE001
                    return {"status": 0, "error": str(exc)}

            out: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=n) as pool:
                futures = [pool.submit(one) for _ in range(n)]
                for f in as_completed(futures):
                    out.append(f.result())
            return out

        results = burst(method)
        statuses = [r.get("status") for r in results]
        if method == "GET" and statuses and all(s in (404, 405, 0) for s in statuses):
            method = "POST"
            results = burst(method)
            statuses = [r.get("status") for r in results]

        success_count = sum(1 for s in statuses if s and 200 <= s < 300)
        distinct = {r.get("body") for r in results if r.get("status") and 200 <= r["status"] < 300}
        evidence: list[str] = []
        if success_count > 1:
            evidence.append(f"{success_count}/{n} request {method} paralel mengembalikan 2xx — kemungkinan race")
            if len(distinct) > 1:
                evidence.append(f"{len(distinct)} bentuk respons sukses berbeda — status berubah di tengah burst")
        observations_extra = [f"method={method}"]

        return self._result(
            task, status="success",
            summary=f"Race burst {n} request {method} ke {url[:70]}",
            evidence=evidence,
            observations=observations_extra + [f"2xx_count={success_count}", f"statuses_sample={statuses[:5]}"],
            risk="high" if success_count > 1 else "low",
        )
