"""Interest scoring -> prioritized targets."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from wth_ai.recon.model import PriorityTarget

if TYPE_CHECKING:
    from wth_ai.recon.normalizer import ReconNormalizer

_KEYWORDS = ("admin", "dev", "staging", "test", "internal", "api", "auth", "login",
             "dashboard", "portal", "debug", "beta", "uat", "jenkins", "git")


def _asset_for_url(url: str) -> str:
    return url.split("?")[0]


def score(normalizer: "ReconNormalizer") -> list[PriorityTarget]:
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    def bump(asset: str, points: int, reason: str) -> None:
        if not asset:
            return
        scores[asset] = scores.get(asset, 0) + points
        reasons.setdefault(asset, [])
        if reason not in reasons[asset]:
            reasons[asset].append(reason)

    # subdomains with interesting keywords
    for sub in normalizer.subdomains.values():
        for kw in _KEYWORDS:
            if kw in sub.host:
                bump(sub.host, 3, f"keyword:{kw}")
        if len(sub.sources) > 1:
            bump(sub.host, 2, "multi-tool-confirmed")

    # http probes: interesting status + auth surface
    for probe in normalizer.http_probes.values():
        asset = _asset_for_url(probe.url)
        if probe.status in (401, 403):
            bump(asset, 4, f"auth-surface:{probe.status}")
        elif probe.status in (200, 301, 302):
            bump(asset, 1, f"alive:{probe.status}")
        elif probe.status >= 500:
            bump(asset, 2, f"server-error:{probe.status}")
        for kw in _KEYWORDS:
            if kw in asset.lower():
                bump(asset, 2, f"keyword:{kw}")

    # missing security headers
    for hp in normalizer.headers.values():
        if hp.missing:
            bump(_asset_for_url(hp.url), min(len(hp.missing), 3), "weak-headers")

    # high-interest params
    for p in normalizer.params.values():
        if p.interest >= 5:
            bump(_asset_for_url(p.url), 3, f"interesting-param:{p.name}")

    # exposed docs/api endpoints
    #
    # `ep.kind` is decided by `js.classify_endpoint()`, which reads a PATH
    # PATTERN inside a javascript bundle. Nothing has actually fetched that
    # path, so calling the result "exposed" is an unverified claim. Verified on
    # the live target: `/openapi.json` was scored `exposed-docs (+3)` while its
    # real response is a 404 HTML page. A path only earns the "exposed" bump
    # once an HTTP probe confirms it serves something; otherwise it is recorded
    # as an explicitly unverified JS path.
    probed = {p.url: p.status for p in normalizer.http_probes.values()}
    for ep in normalizer.endpoints.values():
        if ep.kind in ("docs", "graphql", "api"):
            status = probed.get(ep.url)
            if status is None:
                bump(_asset_for_url(ep.url), 1, f"js-path-{ep.kind}-unverified")
            elif status < 400:
                bump(_asset_for_url(ep.url), 3, f"exposed-{ep.kind}")

    # JS intel + secret candidates
    for js in normalizer.js_intel.values():
        if js.endpoints:
            bump(_asset_for_url(js.url), 2, "js-endpoints")
    for sc in normalizer.secret_candidates.values():
        bump(_asset_for_url(sc.source), 5, f"secret-candidate:{sc.secret_type}")

    # cloud candidates
    for cc in normalizer.cloud_candidates.values():
        bump(cc.asset, 4, f"cloud:{cc.provider}")

    targets = [PriorityTarget(asset=a, score=s, reasons=reasons.get(a, [])) for a, s in scores.items()]
    targets.sort(key=lambda t: t.score, reverse=True)
    return targets


def write_priority(targets: list[PriorityTarget], json_path: Path, csv_path: Path) -> None:
    import json

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps([t.to_dict() for t in targets], indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["asset", "score", "reasons"])
        for t in targets:
            writer.writerow([t.asset, t.score, "; ".join(t.reasons)])
