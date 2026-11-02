"""Builtin technology fingerprinting from headers + body (passive)."""

from __future__ import annotations

import re
from typing import Any

from wth_ai.recon import http as rhttp

_HEADER_SIGNS: tuple[tuple[str, str], ...] = (
    ("server", "server"),
    ("x-powered-by", "x-powered-by"),
    ("x-generator", "generator"),
    ("x-aspnet-version", "asp.net"),
    ("x-drupal-cache", "drupal"),
    ("x-shopify-stage", "shopify"),
)

_BODY_SIGNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wordpress", re.compile(r"/wp-(content|includes)/", re.I)),
    ("drupal", re.compile(r"Drupal\.settings|/sites/default/files", re.I)),
    ("joomla", re.compile(r"/media/jui/|Joomla!", re.I)),
    ("laravel", re.compile(r"laravel_session|XSRF-TOKEN", re.I)),
    ("react", re.compile(r"data-reactroot|__REACT_DEVTOOLS", re.I)),
    ("angular", re.compile(r"ng-version|ng-app", re.I)),
    ("vue", re.compile(r"data-v-[0-9a-f]{8}|__VUE__", re.I)),
    ("nextjs", re.compile(r"/_next/static/|__NEXT_DATA__", re.I)),
)

_META_GEN_RE = re.compile(
    r"""<meta[^>]+name=["']generator["'][^>]+content=["']([^"']+)["']""", re.I
)


def fingerprint(url: str, response: rhttp.HttpResponse | None = None) -> list[dict[str, Any]]:
    resp = response or rhttp.fetch(url, method="GET")
    if not resp.ok:
        return []
    host = rhttp.host_of(resp.url)
    found: dict[str, str] = {}

    hmap = {k.lower(): v for k, v in resp.headers.items()}
    for header, tech in _HEADER_SIGNS:
        if header in hmap:
            val = hmap[header]
            found[tech if header != "server" else val.split("/")[0].strip().lower() or tech] = val

    body = resp.text or ""
    for tech, pat in _BODY_SIGNS:
        if pat.search(body):
            found.setdefault(tech, "")
    gen = _META_GEN_RE.search(body)
    if gen:
        found["generator"] = gen.group(1)

    items: list[dict[str, Any]] = []
    for tech, version in found.items():
        ver = ""
        m = re.search(r"([0-9]+\.[0-9][0-9.]*)", version)
        if m:
            ver = m.group(1)
        items.append({"kind": "fingerprint", "host": host, "technology": tech, "version": ver})
    return items


def run(url: str) -> list[dict[str, Any]]:
    return fingerprint(url)
