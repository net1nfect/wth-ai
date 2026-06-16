"""SSRF executor — URL param probe with safe placeholder."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

SSRF_MARKERS = ("url", "redirect", "next", "callback", "dest", "target", "uri", "link")
PROBE_URL = "http://127.0.0.1:1/wth-ssrf-probe"


class SsrfExecutor(BaseVulnExecutor):
    vuln_type = "ssrf"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        target_param = next((p for p in params if p.lower() in SSRF_MARKERS), None)
        if not target_param:
            for p in params:
                if any(m in p.lower() for m in SSRF_MARKERS):
                    target_param = p
                    break

        if not target_param:
            return self._result(
                task, status="success",
                summary=f"No URL-like param found in {url[:80]}",
                evidence=[], observations=["Try a URL with ?url= or ?redirect= param"],
                risk="low",
            )

        mutated = dict(params)
        mutated[target_param] = [PROBE_URL]
        new_query = urlencode(mutated, doseq=True)
        probe_url = urlunparse(parsed._replace(query=new_query))

        role = self.sessions.role_names()[0] if self.sessions.configured() else "user_a"
        if not self.sessions.configured():
            import requests
            try:
                r = requests.get(probe_url, timeout=10, allow_redirects=False)
                resp = {"status": r.status_code, "body": r.text[:5000], "headers": dict(r.headers)}
            except Exception as exc:
                resp = {"error": str(exc), "status": 0, "body": ""}
        else:
            resp = self.sessions.request(role, "GET", probe_url)

        evidence: list[str] = []
        body = resp.get("body", "").lower()
        oob_hit = self._try_mcp_oob(task)
        if oob_hit:
            evidence.append(f"OOB callback via MCP: {oob_hit}")
        if resp.get("status") in {500, 502, 504}:
            evidence.append(f"Server error on internal URL probe via {target_param}")
        if "connection refused" in body or "127.0.0.1" in body:
            evidence.append(f"Internal network error reflected for param {target_param}")
        if resp.get("headers", {}).get("Location", "").startswith("http://127"):
            evidence.append("Redirect to internal host detected")

        return self._result(
            task, status="success",
            summary=f"SSRF probe on {target_param} → {probe_url[:100]}",
            evidence=evidence,
            observations=[f"status={resp.get('status')}", f"param={target_param}"],
            risk="high" if evidence else "low",
            request_pairs=[{"method": "GET", "url": probe_url, "response_status": resp.get("status"), "body_snippet": resp.get("body", "")[:500]}],
        )

    def _try_mcp_oob(self, task: ExecutorTask) -> str:
        """Optional OOB SSRF check via MCP when enabled."""
        try:
            from wth_ai.mcp.client import MCPClient
            from wth_ai.config import WTHConfig
            mcp = MCPClient(WTHConfig.load())
            if mcp.available():
                result = mcp._oob_callback(session_id=task.id)
                if result.success:
                    return result.output[:200]
        except Exception:
            pass
        return ""
