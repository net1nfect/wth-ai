"""Extract API routes and secrets from JS bundles."""

from __future__ import annotations

import re
from typing import Any

import requests

from wth_ai.redaction import MASKED

ROUTE_PATTERNS = [
    re.compile(r"""['"](/api/[^'"?\s]+)['"]"""),
    re.compile(r"""['"](/v\d+/[^'"?\s]+)['"]"""),
    re.compile(r"""fetch\s*\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""axios\.[a-z]+\s*\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""['"](https?://[^'"]+\.amazonaws\.com[^'"]*)['"]"""),
    re.compile(r"""['"](/graphql[^'"]*)['"]"""),
]


class JsAnalyzer:
    def __init__(self, *, timeout: int = 15) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "WTH-AI-JsAnalyzer/1.0"

    def fetch(self, url: str) -> str:
        try:
            r = self.session.get(url, timeout=self.timeout)
            if r.ok:
                return r.text[:500_000]
        except requests.RequestException:
            pass
        return ""

    def extract_routes(self, content: str, base_url: str = "") -> list[str]:
        found: set[str] = set()
        for pat in ROUTE_PATTERNS:
            for m in pat.findall(content):
                route = m if m.startswith("http") else m
                if route.startswith("/") and base_url:
                    from urllib.parse import urljoin
                    route = urljoin(base_url, route)
                found.add(route)
        return sorted(found)

    def analyze(self, js_urls: list[str], base_url: str = "") -> dict[str, Any]:
        routes: list[str] = []
        secrets: list[str] = []
        for url in js_urls[:30]:
            content = self.fetch(url)
            if not content:
                continue
            routes.extend(self.extract_routes(content, base_url))
            for m in re.findall(r"(?i)(api[_-]?key|aws[_-]?access|secret)\s*[:=]\s*['\"]([^'\"]{8,})['\"]", content):
                # `m[1][:20]` used to be written out -- 20 characters of the
                # secret, which is the ENTIRE AWS access-key id (AKIA + 16) and
                # enough of most other keys to be a leak. Every other surface
                # masks with the canonical constant; do the same here.
                secrets.append(f"{m[0]}={MASKED} @ {url}")
        return {
            "routes": list(dict.fromkeys(routes))[:200],
            "secrets": secrets[:20],
            "analyzed": min(len(js_urls), 30),
        }
