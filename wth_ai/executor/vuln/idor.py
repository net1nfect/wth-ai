"""IDOR/BOLA executor — multi-account object swap."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask


class IdorExecutor(BaseVulnExecutor):
    vuln_type = "idor"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        roles = self.sessions.role_names()
        if len(roles) < 2:
            return self._result(
                task, status="blocked",
                summary="IDOR test needs 2+ accounts: /scope set accounts=userA:pass,userB:pass",
                evidence=[], observations=["Configure test_accounts in scope"],
                risk="low", next_step="/scope set accounts=userA:pass,userB:pass",
            )

        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        id_param = next((p for p in params if p.lower() in {"id", "user_id", "uuid", "account_id"}), None)
        path_ids = re.findall(r"/(\d+|[0-9a-f-]{36})", parsed.path)
        object_id = path_ids[-1] if path_ids else (params[id_param][0] if id_param else "")

        user_a, user_b = roles[0], roles[1]
        baseline = self.sessions.request(user_a, "GET", url)
        cross = self.sessions.request(user_b, "GET", url)
        diff = self.sessions.diff_responses(baseline, cross)

        evidence: list[str] = []
        observations = [f"baseline({user_a}) status={baseline.get('status')}", f"cross({user_b}) status={cross.get('status')}"]
        request_pairs = [
            {"role": user_a, "method": "GET", "url": url, "response_status": baseline.get("status"), "body_snippet": baseline.get("body", "")[:500]},
            {"role": user_b, "method": "GET", "url": url, "response_status": cross.get("status"), "body_snippet": cross.get("body", "")[:500]},
        ]

        if cross.get("status") == 200 and diff.get("bodies_differ") and baseline.get("status") == 200:
            if len(cross.get("body", "")) > 50:
                evidence.append(f"User B received different response for object {object_id or 'unknown'}")
                status = "success"
                risk = "high"
            else:
                status = "success"
                risk = "medium"
                evidence.append("Responses differ — manual review needed")
        elif cross.get("status") == 200 and baseline.get("status") == 403:
            evidence.append("Access control may be enforced (403 vs 200)")
            status = "success"
            risk = "low"
        else:
            status = "success"
            risk = "low"
            observations.append("No clear IDOR signal — may need authenticated session login")

        return self._result(
            task, status=status,
            summary=f"IDOR probe: {user_a} vs {user_b} on {url[:80]}",
            evidence=evidence, observations=observations, risk=risk,
            request_pairs=request_pairs,
        )
