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


def diff_results(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two ``ReconNormalizer.to_dict()`` payloads.

    Returns new assets discovered in ``new`` that were absent in ``old``.
    """
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

    changes = {
        "new_subdomains": added("subdomains", "host"),
        "new_alive_urls": added("http_probes", "url"),
        "new_urls": added("urls", "url"),
        "new_endpoints": added("endpoints", "url"),
        "new_params": [f"{it.get('url')}?{it.get('name')}" for it in new.get("params", [])
                       if f"{it.get('url')}|{it.get('name')}".lower()
                       not in {f"{o.get('url')}|{o.get('name')}".lower() for o in old.get("params", [])}],
        "new_js": added("js_intel", "url"),
        "new_cloud_candidates": [it.get("asset") for it in new.get("cloud_candidates", [])
                                 if it.get("asset", "").lower()
                                 not in _keyset(old.get("cloud_candidates", []), "asset")],
        "new_tls": added("tls", "host"),
    }
    changes["total_changes"] = sum(len(v) for v in changes.values() if isinstance(v, list))
    changes["changed"] = changes["total_changes"] > 0
    return changes
