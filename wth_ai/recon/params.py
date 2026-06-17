"""Builtin parameter & endpoint intelligence (passive parsing of URLs)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

# Parameters that frequently matter for access-control / SSRF / redirect recon.
HIGH_INTEREST = frozenset(
    {
        "id", "user", "user_id", "uid", "account", "account_id", "order", "order_id",
        "doc", "file", "path", "url", "redirect", "redirect_uri", "next", "return",
        "callback", "dest", "destination", "continue", "token", "key", "api_key",
        "email", "role", "admin", "debug", "test", "page", "template", "include",
    }
)

API_HINTS = ("/api/", "/v1/", "/v2/", "/v3/", "/graphql", "/rest/", "/rpc")
DOCS_HINTS = ("swagger", "openapi", "api-docs", "redoc", "graphiql")


def classify_endpoint(url: str) -> str:
    low = url.lower()
    if any(h in low for h in DOCS_HINTS):
        return "docs"
    if "graphql" in low:
        return "graphql"
    if any(h in low for h in API_HINTS):
        return "api"
    return "web"


def extract(urls: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_param: set[str] = set()
    for url in urls:
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        kind = classify_endpoint(url)
        if kind != "web" or parsed.query:
            items.append({"kind": "endpoint", "url": url, "method": "GET", "endpoint_kind": kind})
        for name in parse_qs(parsed.query):
            base = url.split("?")[0]
            dedup = f"{base}|{name}"
            if dedup in seen_param:
                continue
            seen_param.add(dedup)
            interest = 5 if name.lower() in HIGH_INTEREST else 1
            items.append(
                {"kind": "parameter", "url": base, "name": name, "interest": interest}
            )
    return items


def run(urls: list[str]) -> list[dict[str, Any]]:
    return extract(urls)
