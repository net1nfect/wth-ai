"""Parse tool output into structured evidence."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from wth_ai.models import Evidence, ExecutorResult


class EvidencePipeline:
    """Extract and classify evidence from executor output."""

    NUCLEI_RE = re.compile(r"\[(critical|high|medium|low)\]", re.I)

    def from_executor_result(self, result: ExecutorResult) -> list[Evidence]:
        items: list[Evidence] = []
        for line in result.evidence:
            items.append(Evidence(
                id=f"EVD-{uuid4().hex[:8]}",
                source_tool="executor",
                evidence_type=self._classify(line),
                content=line[:500],
                severity_hint=self._severity(line),
                task_id=result.task_id,
            ))
        combined = result.output_summary
        if combined and not items:
            items.append(Evidence(
                id=f"EVD-{uuid4().hex[:8]}",
                source_tool="executor",
                evidence_type="output",
                content=combined[:1000],
                task_id=result.task_id,
            ))
        return items

    def parse_httpx_json(self, raw: str) -> list[Evidence]:
        items = []
        for line in raw.splitlines():
            try:
                data = json.loads(line)
                url = data.get("url", "")
                status = data.get("status_code", "")
                items.append(Evidence(
                    id=f"EVD-{uuid4().hex[:8]}",
                    source_tool="httpx",
                    evidence_type="http_probe",
                    content=f"{url} [{status}]",
                    severity_hint="info",
                ))
            except json.JSONDecodeError:
                continue
        return items

    def diff_responses(self, baseline: str, mutated: str, url: str) -> Evidence | None:
        if baseline == mutated:
            return None
        len_diff = abs(len(baseline) - len(mutated))
        if len_diff < 10:
            return None
        return Evidence(
            id=f"EVD-{uuid4().hex[:8]}",
            source_tool="diff",
            evidence_type="idor_candidate",
            content=f"Response diff on {url}: baseline={len(baseline)}b mutated={len(mutated)}b",
            severity_hint="medium",
        )

    def _classify(self, line: str) -> str:
        low = line.lower()
        if any(k in low for k in ("sql", "syntax error", "mysql")):
            return "sqli_signal"
        if any(k in low for k in ("<script", "xss", "alert(")):
            return "xss_signal"
        if any(k in low for k in ("unauthorized", "forbidden", "403", "401")):
            return "auth_signal"
        if "cve-" in low:
            return "cve"
        return "observation"

    def _severity(self, line: str) -> str:
        m = self.NUCLEI_RE.search(line)
        if m:
            return m.group(1).lower()
        if "critical" in line.lower():
            return "critical"
        if "unauthorized" in line.lower():
            return "medium"
        return "info"

    def to_findings_candidates(self, evidence: list[Evidence]) -> list[dict[str, Any]]:
        candidates = []
        for ev in evidence:
            if ev.severity_hint in {"critical", "high", "medium"} or ev.evidence_type.endswith("_signal"):
                candidates.append({
                    "id": f"FIND-{uuid4().hex[:8]}",
                    "title": f"{ev.evidence_type} via {ev.source_tool}",
                    "severity": ev.severity_hint if ev.severity_hint in {"critical", "high", "medium", "low"} else "medium",
                    "component": ev.source_tool,
                    "affected_asset": ev.content[:100],
                    "description": ev.content,
                    "evidence": [ev.content],
                    "state": "candidate",
                })
        return candidates
