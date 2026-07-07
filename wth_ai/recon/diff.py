"""Diff engine: compare two normalized recon results -> change set."""

from __future__ import annotations

from typing import Any


def _keyset(items: list[dict[str, Any]], field: str) -> set[str]:
    out: set[str] = set()
    for it in items:
        val = it.get(field)
        if isinstance(val, str):
            out.add(val.lower().rstrip("/"))
    return out


def _param_keys(items: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for it in items:
        keys.add(f"{it.get('url', '')}|{it.get('name', '')}".lower())
    return keys


def _header_fingerprint(items: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        url = (it.get("url") or "").lower().rstrip("/")
        present = it.get("present", {})
        missing = sorted(it.get("missing", []))
        out[url] = f"{sorted(present.keys())}|missing:{missing}"
    return out


def _tls_fingerprint(items: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items:
        host = (it.get("host") or "").lower()
        out[host] = f"{it.get('issuer', '')}|{it.get('not_after', '')}|{it.get('subject', '')}"
    return out


def diff_results(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two ``ReconNormalizer.to_dict()`` payloads."""
    old = old or {}
    new = new or {}

    def added(bucket: str, field: str) -> list[str]:
        old_keys = _keyset(old.get(bucket, []), field)
        result: list[str] = []
        for it in new.get(bucket, []):
            v = it.get(field)
            if isinstance(v, str) and v.lower().rstrip("/") not in old_keys:
                result.append(v)
        return result

    old_params = _param_keys(old.get("params", []))
    new_params = [
        f"{it.get('url')}?{it.get('name')}"
        for it in new.get("params", [])
        if f"{it.get('url')}|{it.get('name')}".lower() not in old_params
    ]

    old_headers = _header_fingerprint(old.get("headers", []))
    new_headers = _header_fingerprint(new.get("headers", []))
    header_changes = [
        url for url, fp in new_headers.items()
        if url in old_headers and old_headers[url] != fp
    ]
    header_changes.extend(url for url in new_headers if url not in old_headers)

    old_tls = _tls_fingerprint(old.get("tls", []))
    new_tls = _tls_fingerprint(new.get("tls", []))
    tls_cert_changed = [
        host for host, fp in new_tls.items()
        if host in old_tls and old_tls[host] != fp
    ]

    old_findings = _keyset(old.get("findings", []), "title")
    new_priority = [
        it.get("title", it.get("asset", ""))
        for it in new.get("findings", [])
        if (it.get("title") or "").lower() not in old_findings
        and it.get("severity_hint", "info") in ("medium", "high", "critical")
    ]

    changes = {
        "new_subdomains": added("subdomains", "host"),
        "new_alive_urls": added("http_probes", "url"),
        "new_urls": added("urls", "url"),
        "new_endpoints": added("endpoints", "url"),
        "new_params": new_params,
        "new_js": added("js_intel", "url"),
        "new_cloud_candidates": [
            it.get("asset") for it in new.get("cloud_candidates", [])
            if it.get("asset", "").lower() not in _keyset(old.get("cloud_candidates", []), "asset")
        ],
        "new_tls": added("tls", "host"),
        "new_ports": [
            it.get("title", it.get("detail", ""))
            for it in new.get("findings", [])
            if it.get("category") == "service"
            and (it.get("title") or "").lower() not in old_findings
        ],
        "header_posture_changed": header_changes,
        "tls_cert_changed": tls_cert_changed,
        "new_priority_targets": new_priority,
    }
    changes["total_changes"] = sum(len(v) for v in changes.values() if isinstance(v, list))
    changes["changed"] = changes["total_changes"] > 0
    return changes
