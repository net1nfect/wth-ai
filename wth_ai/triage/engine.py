"""Triage engine (Fase 6, flowchart Stage 8).

Consolidates raw finding candidates into deduplicated, clustered, scope-verified,
severity-ranked findings with fix recommendations and retest tags, attaching an
evidence bundle to each. Emits ``finding.high_priority`` for confirmed-high items.
Everything stays hypothesis-level (``proven=False``) — human review decides.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from wth_ai.evidence.bundle import build_bundle

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# category -> remediation guidance
_FIX_RECOMMENDATIONS: dict[str, str] = {
    "headers": "Add the missing security headers (CSP, HSTS, X-Frame-Options, etc.).",
    "reflection": "Encode/escape user input on output; add CSP; validate parameters.",
    "access_control": "Enforce server-side authorization on every object/route; deny by default.",
    "api": "Restrict access to API schema/docs; require auth; disable in production.",
    "info_disclosure": "Disable verbose errors/debug in production; return generic error pages.",
    "anomaly": "Investigate server errors; add monitoring and input validation.",
    "upload": "Validate file type/size server-side; store outside web root; scan uploads.",
    "websocket": "Authenticate WebSocket handshakes; validate origin; authorize messages.",
}


@dataclass
class TriagedFinding:
    finding_id: str
    title: str
    asset: str
    category: str
    severity: str
    confidence: float
    detail: str
    cluster: str
    fix_recommendation: str
    retest_required: bool
    in_scope: bool
    proven: bool = False
    evidence_bundle: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id, "title": self.title, "asset": self.asset,
            "category": self.category, "severity": self.severity,
            "confidence": round(self.confidence, 3), "detail": self.detail,
            "cluster": self.cluster, "fix_recommendation": self.fix_recommendation,
            "retest_required": self.retest_required, "in_scope": self.in_scope,
            "proven": self.proven, "evidence_bundle": self.evidence_bundle,
        }


class TriageEngine:
    def __init__(self, scope_hosts: set[str] | None = None) -> None:
        self.scope_hosts = {h.lower() for h in (scope_hosts or set())}

    def _in_scope(self, asset: str) -> bool:
        if not self.scope_hosts:
            return True
        host = (urlparse(asset if "://" in asset else f"https://{asset}").hostname or "").lower()
        return any(host == s or host.endswith("." + s) for s in self.scope_hosts)

    @staticmethod
    def _cluster(asset: str) -> str:
        host = (urlparse(asset if "://" in asset else f"https://{asset}").hostname or asset).lower()
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host

    def triage(self, findings: list[dict[str, Any]], *, bus: Any = None,
               job_id: str | None = None) -> dict[str, Any]:
        # 1) dedup (category|asset|title) keeping highest confidence
        deduped: dict[tuple, dict[str, Any]] = {}
        for f in findings:
            key = (f.get("category", ""), f.get("asset", ""), f.get("title", ""))
            cur = deduped.get(key)
            if cur is None or float(f.get("confidence", 0)) > float(cur.get("confidence", 0)):
                deduped[key] = f

        triaged: list[TriagedFinding] = []
        for f in deduped.values():
            asset = f.get("asset", "")
            category = f.get("category", "info")
            severity = f.get("severity", f.get("severity_hint", "info"))
            in_scope = self._in_scope(asset)
            # 2) false-positive filter: out-of-scope findings are dropped
            if not in_scope:
                continue
            # 3) evidence-quality gate: require some evidence for medium+ findings
            ev = f.get("evidence", {}) or {}
            if _SEVERITY_RANK.get(severity, 0) >= 2 and not ev:
                severity = "low"  # downgrade unsupported claims
            bundle = build_bundle(f, graph_node_ref=asset)
            tf = TriagedFinding(
                finding_id=f"FND-{uuid.uuid4().hex[:8]}",
                title=f.get("title", ""), asset=asset, category=category,
                severity=severity, confidence=float(f.get("confidence", 0.0) or 0.0),
                detail=f.get("detail", ""), cluster=self._cluster(asset),
                fix_recommendation=_FIX_RECOMMENDATIONS.get(category, "Review and remediate per policy."),
                retest_required=_SEVERITY_RANK.get(severity, 0) >= 2,
                in_scope=in_scope, proven=bool(f.get("proven", False)),
                evidence_bundle=bundle.to_dict(),
            )
            triaged.append(tf)
            if _SEVERITY_RANK.get(severity, 0) >= 3 and bus is not None:
                try:
                    bus.emit("finding.high_priority", job_id=job_id,
                             data={"title": tf.title, "asset": tf.asset, "severity": severity,
                                   "finding_id": tf.finding_id})
                except Exception:  # noqa: BLE001
                    pass

        triaged.sort(key=lambda t: (_SEVERITY_RANK.get(t.severity, 0), t.confidence), reverse=True)
        clusters: dict[str, int] = {}
        sev_counts: dict[str, int] = {}
        for t in triaged:
            clusters[t.cluster] = clusters.get(t.cluster, 0) + 1
            sev_counts[t.severity] = sev_counts.get(t.severity, 0) + 1
        return {
            "findings": [t.to_dict() for t in triaged],
            "count": len(triaged),
            "dropped_out_of_scope": len(deduped) - len(triaged),
            "clusters": clusters,
            "severity_counts": sev_counts,
        }
