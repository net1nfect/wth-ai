"""Result normalizer: fold tool outputs into deduplicated typed assets.

Every normalized item carries source-tool attribution and a confidence score
that grows as independent tools confirm the same asset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    ToolRun,
    URLAsset,
)


class ReconNormalizer:
    def __init__(self) -> None:
        self.subdomains: dict[str, DomainAsset] = {}
        self.dns_records: dict[str, DNSRecord] = {}
        self.urls: dict[str, URLAsset] = {}
        self.http_probes: dict[str, HTTPProbe] = {}
        self.headers: dict[str, HeaderPosture] = {}
        self.cookies: dict[str, CookiePosture] = {}
        self.tls: dict[str, TLSInfo] = {}
        self.fingerprints: dict[str, TechnologyFingerprint] = {}
        self.endpoints: dict[str, Endpoint] = {}
        self.params: dict[str, Parameter] = {}
        self.js_intel: dict[str, JSIntel] = {}
        self.secret_candidates: dict[str, SecretCandidateMasked] = {}
        self.cloud_candidates: dict[str, CloudCandidate] = {}
        self.findings: dict[str, FindingCandidate] = {}

    # --- ingestion ---
    def add_run(self, run: ToolRun) -> None:
        for item in run.normalized_items:
            self.add_item(run.tool, item)

    def add_item(self, source: str, item: dict[str, Any]) -> None:
        kind = item.get("kind")
        handler = getattr(self, f"_add_{kind}", None)
        if handler:
            handler(source, item)

    def _bucket_add(self, bucket: dict[str, Any], obj: Any, source: str) -> Any:
        existing = bucket.get(obj.key())
        if existing is None:
            bucket[obj.key()] = obj
            obj.merge_source(source)
            return obj
        existing.merge_source(source)
        return existing

    # --- per-kind handlers ---
    @staticmethod
    def _mask(value: Any) -> str:
        """Mask credentials in a discovered URL before it becomes an asset.

        Every writer downstream reads from these buckets -- normalized/*.json,
        normalized_result.json, asset_graph.json, recon_summary.json,
        recon_report.md, the job result and the change events -- so masking at
        this single boundary keeps a discovered credential out of all of them.
        (Verified pre-fix: a URL carrying `?api_key=AKIA...` was stored verbatim
        in all seven while the SAME key showed up correctly masked in
        secret_candidates.json.)
        """
        from wth_ai.redaction import mask_url_secrets

        return mask_url_secrets(str(value or ""))

    def _add_subdomain(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(self.subdomains, DomainAsset(host=it["host"]), source)

    def _add_dns_record(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(
            self.dns_records,
            DNSRecord(host=it["host"], record_type=it.get("record_type", "A"), value=it.get("value", "")),
            source,
        )

    def _add_url(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(self.urls, URLAsset(url=self._mask(it["url"])), source)

    def _add_http_probe(self, source: str, it: dict[str, Any]) -> None:
        obj = self._bucket_add(self.http_probes, HTTPProbe(url=self._mask(it["url"])), source)
        # enrich on first real data
        if it.get("status"):
            obj.status = it["status"]
        obj.title = obj.title or it.get("title", "")
        obj.server = obj.server or it.get("server", "")
        obj.content_type = obj.content_type or it.get("content_type", "")
        if it.get("length"):
            obj.length = it["length"]

    def _add_header(self, source: str, it: dict[str, Any]) -> None:
        obj = self._bucket_add(self.headers, HeaderPosture(url=self._mask(it["url"])), source)
        obj.missing = it.get("missing", obj.missing)
        obj.present = it.get("present", obj.present)

    def _add_cookie(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(
            self.cookies,
            CookiePosture(
                url=self._mask(it["url"]), name=it.get("name", ""), secure=it.get("secure", False),
                httponly=it.get("httponly", False), samesite=it.get("samesite", ""),
            ),
            source,
        )

    def _add_tls(self, source: str, it: dict[str, Any]) -> None:
        obj = self._bucket_add(self.tls, TLSInfo(host=it["host"]), source)
        obj.subject = it.get("subject", obj.subject)
        obj.issuer = it.get("issuer", obj.issuer)
        obj.not_after = it.get("not_after", obj.not_after)
        obj.san = it.get("san", obj.san)

    def _add_fingerprint(self, source: str, it: dict[str, Any]) -> None:
        obj = self._bucket_add(
            self.fingerprints,
            TechnologyFingerprint(host=it.get("host", ""), technology=it.get("technology", "")),
            source,
        )
        obj.version = obj.version or it.get("version", "")

    def _add_endpoint(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(
            self.endpoints,
            Endpoint(
                url=self._mask(it["url"]), method=it.get("method", "GET"),
                kind=it.get("endpoint_kind", "web"),
            ),
            source,
        )

    def _add_parameter(self, source: str, it: dict[str, Any]) -> None:
        obj = self._bucket_add(
            self.params, Parameter(url=self._mask(it["url"]), name=it.get("name", "")), source
        )
        obj.interest = max(obj.interest, it.get("interest", 0))

    def _add_js_intel(self, source: str, it: dict[str, Any]) -> None:
        obj = self._bucket_add(self.js_intel, JSIntel(url=self._mask(it["url"])), source)
        for ep in it.get("endpoints", []):
            masked = self._mask(ep)
            if masked not in obj.endpoints:
                obj.endpoints.append(masked)

    def _add_secret_candidate(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(
            self.secret_candidates,
            SecretCandidateMasked(
                source=it.get("source", ""), secret_type=it.get("secret_type", ""),
                masked=it.get("masked", "***"),
            ),
            source,
        )

    def _add_cloud_candidate(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(
            self.cloud_candidates,
            CloudCandidate(provider=it.get("provider", ""), asset=it.get("asset", "")),
            source,
        )

    def _add_finding(self, source: str, it: dict[str, Any]) -> None:
        self._bucket_add(
            self.findings,
            FindingCandidate(
                title=it.get("title", ""), asset=it.get("asset", ""),
                category=it.get("category", "info"), severity_hint=it.get("severity_hint", "info"),
                detail=it.get("detail", ""),
            ),
            source,
        )

    # --- export ---
    def summary(self) -> dict[str, int]:
        return {
            "subdomains": len(self.subdomains),
            "dns_records": len(self.dns_records),
            "urls": len(self.urls),
            "http_probes": len(self.http_probes),
            "headers": len(self.headers),
            "cookies": len(self.cookies),
            "tls": len(self.tls),
            "fingerprints": len(self.fingerprints),
            "endpoints": len(self.endpoints),
            "params": len(self.params),
            "js_intel": len(self.js_intel),
            "secret_candidates": len(self.secret_candidates),
            "cloud_candidates": len(self.cloud_candidates),
            "findings": len(self.findings),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "subdomains": [m.to_dict() for m in self.subdomains.values()],
            "dns_records": [m.to_dict() for m in self.dns_records.values()],
            "urls": [m.to_dict() for m in self.urls.values()],
            "http_probes": [m.to_dict() for m in self.http_probes.values()],
            "headers": [m.to_dict() for m in self.headers.values()],
            "cookies": [m.to_dict() for m in self.cookies.values()],
            "tls": [m.to_dict() for m in self.tls.values()],
            "fingerprints": [m.to_dict() for m in self.fingerprints.values()],
            "endpoints": [m.to_dict() for m in self.endpoints.values()],
            "params": [m.to_dict() for m in self.params.values()],
            "js_intel": [m.to_dict() for m in self.js_intel.values()],
            "secret_candidates": [m.to_dict() for m in self.secret_candidates.values()],
            "cloud_candidates": [m.to_dict() for m in self.cloud_candidates.values()],
            "findings": [m.to_dict() for m in self.findings.values()],
        }

    def write_normalized(self, normalized_dir: Path) -> None:
        normalized_dir.mkdir(parents=True, exist_ok=True)

        def _w(name: str, data: Any) -> None:
            (normalized_dir / name).write_text(json.dumps(data, indent=2), encoding="utf-8")

        _w("subdomains.json", [m.to_dict() for m in self.subdomains.values()])
        _w("dns_records.json", [m.to_dict() for m in self.dns_records.values()])
        _w("alive_urls.json", [m.to_dict() for m in self.http_probes.values()])
        _w("crawl_urls.json", [m.to_dict() for m in self.urls.values()])
        _w(
            "discovered_paths.json",
            sorted({urlparse(u.url).path or "/" for u in self.urls.values()}),
        )
        _w(
            "headers_cookies.json",
            {
                "headers": [m.to_dict() for m in self.headers.values()],
                "cookies": [m.to_dict() for m in self.cookies.values()],
            },
        )
        _w("tls.json", [m.to_dict() for m in self.tls.values()])
        _w("fingerprint.json", [m.to_dict() for m in self.fingerprints.values()])
        _w("js_intel.json", [m.to_dict() for m in self.js_intel.values()])
        _w("params.json", [m.to_dict() for m in self.params.values()])
        _w("cloud_candidates.json", [m.to_dict() for m in self.cloud_candidates.values()])
        _w("endpoints.json", [m.to_dict() for m in self.endpoints.values()])
        _w("secret_candidates.json", [m.to_dict() for m in self.secret_candidates.values()])
        _w("findings.json", [m.to_dict() for m in self.findings.values()])
