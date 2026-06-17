"""Builtin crawler & content discovery (low-rate, non-aggressive).

Fetches the homepage + a tiny set of well-known safe paths and extracts links,
JS files, and forms. Strictly recon-only: small page budget, GET only.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from wth_ai.recon import http as rhttp
from wth_ai.recon.wordlists import safe_paths

_HREF_RE = re.compile(r"""(?:href|src|action)\s*=\s*["']([^"'#\s]+)["']""", re.IGNORECASE)
_FORM_RE = re.compile(r"<form\b[^>]*>", re.IGNORECASE)


def _same_host(base: str, link: str) -> bool:
    try:
        b = urlparse(base).hostname or ""
        l = urlparse(link).hostname or ""
        return (l == "" ) or (l.lower() == b.lower())
    except ValueError:
        return False


def extract_links(base_url: str, html: str) -> list[str]:
    links: set[str] = set()
    for m in _HREF_RE.finditer(html or ""):
        raw = m.group(1).strip()
        if raw.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(base_url, raw)
        if absolute.startswith("http") and _same_host(base_url, absolute):
            links.add(absolute.split("#")[0])
    return sorted(links)


def crawl(base_url: str, *, max_pages: int = 8, probe_safe_paths: bool = True) -> list[dict[str, Any]]:
    base_url = rhttp.normalize_url(base_url)
    items: list[dict[str, Any]] = []
    visited: set[str] = set()
    queue: list[str] = [base_url]

    # seed a few well-known safe paths
    if probe_safe_paths:
        for p in safe_paths():
            queue.append(urljoin(base_url + "/", p))

    pages = 0
    while queue and pages < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        resp = rhttp.fetch(url, method="GET")
        if not resp.ok:
            continue
        pages += 1
        items.append({"kind": "url", "url": resp.url})
        ctype = resp.headers.get("Content-Type", resp.headers.get("content-type", ""))
        if "html" in ctype.lower() or resp.text.lstrip().startswith("<"):
            links = extract_links(resp.url, resp.text)
            for link in links[:50]:
                items.append({"kind": "url", "url": link})
            # forms hint at parameters/endpoints
            if _FORM_RE.search(resp.text):
                items.append(
                    {"kind": "endpoint", "url": resp.url, "method": "POST", "endpoint_kind": "web"}
                )
    return items


def run(base_url: str, *, max_pages: int = 8) -> list[dict[str, Any]]:
    return crawl(base_url, max_pages=max_pages)
