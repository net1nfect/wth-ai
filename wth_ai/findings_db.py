"""Structured findings database."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from wth_ai.models import Finding, FindingState, Hypothesis, utc_now


class FindingsDB:
    """In-memory findings and hypotheses store."""

    def __init__(self) -> None:
        self.findings: dict[str, Finding] = {}
        self.hypotheses: dict[str, Hypothesis] = {}

    def add_hypothesis(self, data: dict[str, Any]) -> Hypothesis:
        hid = data.get("id") or f"HYP-{uuid4().hex[:8]}"
        hyp = Hypothesis(
            id=hid,
            title=data.get("title", "Untitled"),
            component=data.get("component", ""),
            assumption=data.get("assumption", data.get("security_assumption", "")),
            why_suspicious=data.get("why_suspicious", ""),
            evidence_needed=data.get("evidence_needed", ""),
            safe_test=data.get("safe_test", ""),
            impact_if_true=data.get("impact_if_true", ""),
            priority=int(data.get("priority", 5)),
            state=data.get("state", "open"),
        )
        self.hypotheses[hid] = hyp
        return hyp

    def add_finding(self, data: dict[str, Any]) -> Finding:
        fid = data.get("id") or f"FIND-{uuid4().hex[:8]}"
        state_raw = data.get("state", "candidate")
        state = FindingState(state_raw) if state_raw in {s.value for s in FindingState} else FindingState.CANDIDATE
        finding = Finding(
            id=fid,
            title=data.get("title", "Untitled"),
            severity=data.get("severity", "info"),
            state=state,
            component=data.get("component", ""),
            affected_asset=data.get("affected_asset", ""),
            description=data.get("description", ""),
            evidence=data.get("evidence", []),
            impact=data.get("impact", ""),
            root_cause=data.get("root_cause", data.get("root_cause_hypothesis", "")),
            remediation=data.get("remediation", data.get("remediation_idea", "")),
            cwe=data.get("cwe", data.get("CWE/OWASP category", "")),
        )
        self.findings[fid] = finding
        return finding

    def update_finding_state(self, finding_id: str, state: FindingState | str, **kwargs: Any) -> Finding | None:
        if finding_id not in self.findings:
            return None
        finding = self.findings[finding_id]
        if isinstance(state, str):
            state = FindingState(state) if state in {s.value for s in FindingState} else finding.state
        finding.state = state
        finding.updated_at = utc_now()
        for key, value in kwargs.items():
            if hasattr(finding, key) and value is not None:
                setattr(finding, key, value)
        return finding

    def list_findings(self, state: str | None = None) -> list[Finding]:
        items = list(self.findings.values())
        if state:
            items = [f for f in items if f.state.value == state or f.state == state]
        return sorted(items, key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(f.severity, 5))

    def list_hypotheses(self) -> list[Hypothesis]:
        return sorted(self.hypotheses.values(), key=lambda h: h.priority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings.values()],
            "hypotheses": [h.to_dict() for h in self.hypotheses.values()],
        }

    def load_from_dict(self, data: dict[str, Any]) -> None:
        for h in data.get("hypotheses", []):
            self.add_hypothesis(h)
        for f in data.get("findings", []):
            self.add_finding(f)

    def format_summary_table(self) -> str:
        findings = self.list_findings()
        if not findings:
            return "FINDINGS: (kosong)"
        lines = ["| ID | Severity | Title | State | Component |", "|----|----------|-------|-------|-----------|"]
        for f in findings:
            lines.append(f"| {f.id} | {f.severity} | {f.title} | {f.state.value} | {f.component} |")
        return "\n".join(lines)

    def format_hypotheses(self) -> str:
        hyps = self.list_hypotheses()
        if not hyps:
            return "HIPOTESIS: (kosong)"
        lines = ["HIPOTESIS:"]
        for h in hyps:
            lines.append(f"- [{h.id}] P{h.priority} {h.title} — {h.component} ({h.state})")
        return "\n".join(lines)
