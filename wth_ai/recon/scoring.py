"""Risk, interest & prioritization engine (Fase 6, flowchart Stage 4).

Twelve scoring signals (F1-F12) combine into a confidence-adjusted risk score
per asset, a severity tier, and a risk heatmap. The engine is deterministic and
fully offline; the optional LLM layer (Fase 6 P4) augments — never replaces —
these signals.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING

from wth_ai.recon.model import PriorityTarget

if TYPE_CHECKING:
    from wth_ai.recon.normalizer import ReconNormalizer

# F5: admin/dev/internal keyword surface
_KEYWORDS = ("admin", "dev", "staging", "test", "internal", "api", "auth", "login",
             "dashboard", "portal", "debug", "beta", "uat", "jenkins", "git",
             "graphql", "swagger", "upload", "payment", "billing", "account")
_HIGH_VALUE_KEYWORDS = ("admin", "internal", "payment", "billing", "jenkins", "debug", "staging")

# Severity tiers by final score.
_SEVERITY_TIERS: tuple[tuple[int, str], ...] = (
    (12, "critical"), (8, "high"), (5, "medium"), (2, "low"), (0, "info"),
)


def _asset_for_url(url: str) -> str:
    return url.split("?")[0]


def _severity_for(score: int) -> str:
    for threshold, tier in _SEVERITY_TIERS:
        if score >= threshold:
            return tier
    return "info"


def score(normalizer: "ReconNormalizer") -> list[PriorityTarget]:
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}
    confirmations: dict[str, int] = {}

    def bump(asset: str, points: float, reason: str) -> None:
        if not asset:
            return
        scores[asset] = scores.get(asset, 0.0) + points
        reasons.setdefault(asset, [])
        if reason not in reasons[asset]:
            reasons[asset].append(reason)

    def confirm(asset: str, n: int = 1) -> None:
        confirmations[asset] = confirmations.get(asset, 0) + n

    # F1 Exposure + F5 keyword (subdomains)
    for sub in normalizer.subdomains.values():
        for kw in _KEYWORDS:
            if kw in sub.host:
                bump(sub.host, 3 if kw in _HIGH_VALUE_KEYWORDS else 2, f"keyword:{kw}")
        if len(sub.sources) > 1:
            bump(sub.host, 2, "multi-tool-confirmed")  # F? confirmation
            confirm(sub.host, len(sub.sources))

    # F1 Exposure + F2 auth-surface (http probes)
    for probe in normalizer.http_probes.values():
        asset = _asset_for_url(probe.url)
        if probe.status in (401, 403):
            bump(asset, 4, f"auth-surface:{probe.status}")  # F2
        elif probe.status in (200, 301, 302):
            bump(asset, 1, f"alive:{probe.status}")  # F1
        elif probe.status >= 500:
            bump(asset, 2, f"server-error:{probe.status}")  # F9 anomaly
        for kw in _KEYWORDS:
            if kw in asset.lower():
                bump(asset, 2 if kw in _HIGH_VALUE_KEYWORDS else 1, f"keyword:{kw}")

    # F? weak security headers
    for hp in normalizer.headers.values():
        if hp.missing:
            bump(_asset_for_url(hp.url), min(len(hp.missing), 3), "weak-headers")

    # F? interesting parameters (auth/redirect/id surfaces)
    for p in normalizer.params.values():
        if p.interest >= 5:
            bump(_asset_for_url(p.url), 3, f"interesting-param:{p.name}")

    # F8 API depth/versioning + exposed docs/schema
    for ep in normalizer.endpoints.values():
        if ep.kind in ("docs", "graphql", "api", "api_schema"):
            bump(_asset_for_url(ep.url), 3, f"exposed-{ep.kind}")  # F8
        elif ep.kind == "auth":
            bump(_asset_for_url(ep.url), 2, "auth-endpoint")  # F2
        elif ep.kind == "upload":
            bump(_asset_for_url(ep.url), 4, "upload-surface")  # F7
        elif ep.kind == "websocket":
            bump(_asset_for_url(ep.url), 2, "websocket-surface")

    # JS intel + F6 secret/cloud exposure
    for js in normalizer.js_intel.values():
        if js.endpoints:
            bump(_asset_for_url(js.url), 2, "js-endpoints")
    for sc in normalizer.secret_candidates.values():
        bump(_asset_for_url(sc.source), 5, f"secret-candidate:{sc.secret_type}")  # F6
    for cc in normalizer.cloud_candidates.values():
        weight = 4 if cc.provider != "third_party" else 1
        bump(cc.asset, weight, f"cloud:{cc.provider}")  # F6

    # F? finding candidates raise interest of their asset
    for fc in normalizer.findings.values():
        sev = {"critical": 6, "high": 5, "medium": 3, "low": 2}.get(fc.severity_hint, 1)
        bump(_asset_for_url(fc.asset), sev, f"finding:{fc.category}")

    # F10 confidence-adjusted risk + severity tiering
    targets: list[PriorityTarget] = []
    for asset, raw in scores.items():
        conf = min(1.0, 0.5 + 0.1 * confirmations.get(asset, 0))
        adjusted = int(round(raw * (0.8 + 0.4 * conf)))  # confidence nudges +/-20%
        targets.append(PriorityTarget(
            asset=asset, score=adjusted, reasons=reasons.get(asset, []),
            severity=_severity_for(adjusted), confidence=conf,
            signals=reasons.get(asset, []),
        ))
    targets.sort(key=lambda t: t.score, reverse=True)
    return targets


def risk_heatmap(targets: list[PriorityTarget]) -> dict[str, list[dict]]:
    """F11: bucket prioritized targets into a severity heatmap."""
    buckets: dict[str, list[dict]] = {"critical": [], "high": [], "medium": [], "low": [], "info": []}
    for t in targets:
        buckets.setdefault(t.severity, []).append({"asset": t.asset, "score": t.score})
    return buckets


def write_priority(targets: list[PriorityTarget], json_path: Path, csv_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps([t.to_dict() for t in targets], indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["asset", "score", "severity", "confidence", "reasons"])
        for t in targets:
            writer.writerow([t.asset, t.score, t.severity, round(t.confidence, 3),
                             "; ".join(t.reasons)])


def write_heatmap(targets: list[PriorityTarget], path: Path) -> dict[str, list[dict]]:
    heat = risk_heatmap(targets)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(heat, indent=2), encoding="utf-8")
    return heat
