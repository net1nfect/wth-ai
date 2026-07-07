"""Bridge recon job results into session AssetGraph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wth_ai.graph.adapters import normalized_to_asset_graph
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.recon.model import (
    CloudCandidate,
    CookiePosture,
    DNSRecord,
    DomainAsset,
    Endpoint,
    FindingCandidate,
    HeaderPosture,
    HTTPProbe,
    JSIntel,
    Parameter,
    SecretCandidateMasked,
    TechnologyFingerprint,
    TLSInfo,
    URLAsset,
)
from wth_ai.recon.normalizer import ReconNormalizer


def normalizer_from_dict(data: dict[str, Any]) -> ReconNormalizer:
    """Rebuild a ReconNormalizer from ``to_dict()`` / ``normalized_result.json``."""
    norm = ReconNormalizer()

    def _load(bucket: dict[str, Any], items: list[dict[str, Any]], factory: Any) -> None:
        for raw in items or []:
            obj = factory(**raw) if isinstance(raw, dict) else factory(raw)
            bucket[obj.key()] = obj

    for raw in data.get("subdomains", []):
        obj = DomainAsset(host=raw["host"])
        norm.subdomains[obj.key()] = obj
    for raw in data.get("dns_records", []):
        obj = DNSRecord(host=raw["host"], record_type=raw.get("record_type", "A"), value=raw.get("value", ""))
        norm.dns_records[obj.key()] = obj
    for raw in data.get("urls", []):
        obj = URLAsset(url=raw["url"])
        norm.urls[obj.key()] = obj
    for raw in data.get("http_probes", []):
        obj = HTTPProbe(url=raw["url"], status=int(raw.get("status", 0) or 0))
        norm.http_probes[obj.key()] = obj
    for raw in data.get("headers", []):
        obj = HeaderPosture(
            url=raw["url"],
            missing=raw.get("missing", []),
            present=raw.get("present", raw.get("headers", {})),
        )
        norm.headers[obj.key()] = obj
    for raw in data.get("cookies", []):
        obj = CookiePosture(
            url=raw["url"],
            name=raw.get("name", ""),
            secure=bool(raw.get("secure", False)),
            httponly=bool(raw.get("httponly", False)),
            samesite=raw.get("samesite", ""),
        )
        norm.cookies[obj.key()] = obj
    for raw in data.get("tls", []):
        obj = TLSInfo(host=raw["host"], issuer=raw.get("issuer", ""), not_after=raw.get("not_after", ""))
        norm.tls[obj.key()] = obj
    for raw in data.get("fingerprints", []):
        obj = TechnologyFingerprint(
            host=raw.get("host", ""),
            technology=raw.get("technology", ""),
            version=raw.get("version", ""),
        )
        norm.fingerprints[obj.key()] = obj
    for raw in data.get("endpoints", []):
        obj = Endpoint(url=raw["url"], method=raw.get("method", "GET"))
        norm.endpoints[obj.key()] = obj
    for raw in data.get("params", []):
        obj = Parameter(url=raw["url"], name=raw.get("name", raw.get("param", "")))
        norm.params[obj.key()] = obj
    for raw in data.get("js_intel", []):
        obj = JSIntel(url=raw["url"], endpoints=raw.get("endpoints", []))
        norm.js_intel[obj.key()] = obj
    for raw in data.get("secret_candidates", []):
        obj = SecretCandidateMasked(
            source=raw.get("source", ""),
            secret_type=raw.get("secret_type", ""),
            masked=raw.get("masked", raw.get("snippet", "***")),
        )
        norm.secret_candidates[obj.key()] = obj
    for raw in data.get("cloud_candidates", []):
        obj = CloudCandidate(asset=raw.get("asset", ""), provider=raw.get("provider", ""))
        norm.cloud_candidates[obj.key()] = obj
    for raw in data.get("findings", []):
        obj = FindingCandidate(
            title=raw.get("title", ""),
            asset=raw.get("asset", ""),
            category=raw.get("category", ""),
            severity_hint=raw.get("severity_hint", "info"),
            detail=raw.get("detail", ""),
        )
        norm.findings[obj.key()] = obj
    return norm


def load_normalized_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sync_recon_to_graph(
    output_dir: str | Path,
    graph: AssetGraph,
    *,
    target: str = "",
) -> dict[str, Any]:
    """Load recon artifacts and populate the session asset graph."""
    base = Path(output_dir)
    norm_path = base / "normalized_result.json"
    data = load_normalized_result(norm_path)
    if data is None:
        norm_dir = base / "normalized"
        if norm_dir.exists():
            merged: dict[str, Any] = {}
            for name in (
                "subdomains", "dns_records", "urls", "http_probes", "headers",
                "cookies", "tls", "fingerprints", "endpoints", "params",
                "js_intel", "secret_candidates", "cloud_candidates", "findings",
            ):
                fp = norm_dir / f"{name}.json"
                if name == "headers":
                    hc = norm_dir / "headers_cookies.json"
                    if hc.exists():
                        try:
                            blob = json.loads(hc.read_text(encoding="utf-8"))
                            merged["headers"] = blob.get("headers", [])
                            merged["cookies"] = blob.get("cookies", [])
                        except (OSError, json.JSONDecodeError):
                            pass
                    continue
                if fp.exists():
                    try:
                        merged[name] = json.loads(fp.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
            if merged:
                data = merged
    if not data:
        return {"synced": False, "reason": "no normalized data"}

    normalizer = normalizer_from_dict(data)
    normalized_to_asset_graph(normalizer, graph, target=target or "")
    coverage = graph.coverage()
    return {
        "synced": True,
        "nodes": coverage.get("nodes", 0),
        "edges": coverage.get("edges", 0),
        "summary": normalizer.summary(),
    }
