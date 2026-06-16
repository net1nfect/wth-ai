"""XSS executor — reflection probe with harmless marker."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from wth_ai.executor.vuln.base import BaseVulnExecutor
from wth_ai.models import ExecutorResult, ExecutorTask

MARKER = "wthXSSprobe9z8y7"


class XssExecutor(BaseVulnExecutor):
    vuln_type = "xss"

    def execute(self, task: ExecutorTask) -> ExecutorResult:
        url = task.target
        if not url.startswith("http"):
            url = f"https://{url}"
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if not params:
            return self._result(
                task, status="success", summary="No query params to probe",
                evidence=[], observations=[], risk="low",
            )

        param = next(iter(params))
        payload = f'"><{MARKER}/'
        mutated = dict(params)
        mutated[param] = [payload]
        probe_url = urlunparse(parsed._replace(query=urlencode(mutated, doseq=True)))

        import requests
        try:
            r = requests.get(probe_url, timeout=10)
            body = r.text
            status = r.status_code
        except Exception as exc:
            return self._result(task, status="failed", summary=str(exc), evidence=[], observations=[], risk="low")

        evidence: list[str] = []
        context = "unknown"
        if MARKER in body:
            idx = body.find(MARKER)
            snippet = body[max(0, idx - 40):idx + len(MARKER) + 40]
            if f">{MARKER}" in snippet or f'"{MARKER}' in snippet:
                context = "html_unescaped"
                evidence.append(f"Marker reflected unescaped in param `{param}`")
            else:
                context = "reflected_encoded"
                evidence.append(f"Marker reflected (possibly encoded) in param `{param}`")

        return self._result(
            task, status="success",
            summary=f"XSS reflection probe on `{param}` — context={context}",
            evidence=evidence,
            observations=[f"status={status}", f"reflected={MARKER in body}"],
            risk="high" if context == "html_unescaped" else "low",
            request_pairs=[{"method": "GET", "url": probe_url, "response_status": status, "body_snippet": body[:500]}],
        )
