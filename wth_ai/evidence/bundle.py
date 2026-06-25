"""Evidence bundle builder (Fase 6, flowchart node EV).

Turns a finding candidate (from the execution / analysis engines) into a
structured, hash-stamped evidence bundle (fields EV1-EV10). Secrets are never
stored in the clear (the upstream modules already mask them).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EvidenceBundle:
    finding_title: str
    asset: str
    request_metadata: dict[str, Any] = field(default_factory=dict)   # EV1
    response_fingerprint: dict[str, Any] = field(default_factory=dict)  # EV2
    header_snapshot: dict[str, Any] = field(default_factory=dict)    # EV3
    body_hash: str = ""           # EV4
    body_size: int = 0            # EV4
    screenshot_ref: str | None = None  # EV5
    graph_node_ref: str | None = None  # EV6
    reproduction_steps: list[str] = field(default_factory=list)  # EV7
    severity_candidate: str = "info"   # EV8
    confidence: float = 0.0            # EV9
    artifact_sha256: str = ""          # EV10
    bundle_id: str = field(default_factory=lambda: f"EVB-{uuid.uuid4().hex[:10]}")
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "bundle_id": self.bundle_id,
            "created_at": self.created_at,
            "finding_title": self.finding_title,
            "asset": self.asset,
            "request_metadata": self.request_metadata,
            "response_fingerprint": self.response_fingerprint,
            "header_snapshot": self.header_snapshot,
            "body_hash": self.body_hash,
            "body_size": self.body_size,
            "screenshot_ref": self.screenshot_ref,
            "graph_node_ref": self.graph_node_ref,
            "reproduction_steps": self.reproduction_steps,
            "severity_candidate": self.severity_candidate,
            "confidence": round(self.confidence, 3),
        }
        # EV10: stable hash over the bundle content (excluding the hash itself).
        d["artifact_sha256"] = _sha256(json.dumps(d, sort_keys=True, ensure_ascii=False))
        self.artifact_sha256 = d["artifact_sha256"]
        return d


def _repro_steps(finding: dict[str, Any]) -> list[str]:
    ev = finding.get("evidence", {}) or {}
    req = ev.get("request", {}) or {}
    method = req.get("method", "GET")
    url = req.get("url") or finding.get("asset", "")
    steps = [f"1. Send `{method} {url}` (non-destructive).",
             "2. Observe the response status, headers, and body fingerprint below.",
             "3. Compare against the documented baseline to confirm the hypothesis."]
    if req.get("param"):
        steps.insert(1, f"   - Parameter under test: `{req['param']}` (inert canary).")
    return steps


def build_bundle(finding: dict[str, Any], *, graph_node_ref: str | None = None,
                 screenshot_ref: str | None = None) -> EvidenceBundle:
    ev = finding.get("evidence", {}) or {}
    resp = ev.get("response", {}) or {}
    bundle = EvidenceBundle(
        finding_title=finding.get("title", ""),
        asset=finding.get("asset", ""),
        request_metadata=ev.get("request", {}) or {},
        response_fingerprint={
            "status": resp.get("status") or ev.get("anon", {}).get("status"),
            "length": resp.get("length"),
            "content_type": resp.get("content_type", ""),
            "server": resp.get("server", ""),
        },
        header_snapshot={"missing_headers": ev.get("missing_headers", [])},
        body_hash=ev.get("body_sha256", ""),
        body_size=int(resp.get("length") or 0),
        screenshot_ref=screenshot_ref,
        graph_node_ref=graph_node_ref or finding.get("asset"),
        reproduction_steps=_repro_steps(finding),
        severity_candidate=finding.get("severity", finding.get("severity_hint", "info")),
        confidence=float(finding.get("confidence", 0.0) or 0.0),
    )
    return bundle
