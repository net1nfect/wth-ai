"""Builtin JavaScript intelligence: endpoint extraction + masked secret clues.

Non-destructive: only parses already-fetched JS/HTML text. Secret *candidates*
are always masked — the full value is never stored, logged, or used.
"""

from __future__ import annotations

import re
from typing import Any

# Relative/absolute path-like strings inside JS.
_ENDPOINT_RE = re.compile(r"""["'`](/[A-Za-z0-9_\-/.]{2,}|https?://[^"'`\s]{4,})["'`]""")
_JS_FILE_RE = re.compile(r"""["'`]([^"'`\s]+?\.js(?:\?[^"'`\s]*)?)["'`]""")

# Secret-shaped tokens. We only record the *type* + a masked preview.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}")),
    ("generic_api_key", re.compile(r"(?i)(?:api[_-]?key|secret|token)[\"'`]?\s*[:=]\s*[\"'`][A-Za-z0-9_\-]{16,}[\"'`]")),
)

_NOISE = ("/", "//", "http://", "https://")


def mask_secret(value: str) -> str:
    """Mask a secret-like value, keeping only a short prefix hint."""
    value = value.strip().strip("\"'`")
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-2:]}" if len(value) > 12 else value[:3] + "***"


def extract_endpoints(js_text: str) -> list[str]:
    found: set[str] = set()
    for m in _ENDPOINT_RE.finditer(js_text or ""):
        ep = m.group(1)
        if ep in _NOISE or ep.endswith((".png", ".jpg", ".svg", ".css", ".woff", ".woff2")):
            continue
        found.add(ep)
    return sorted(found)


def extract_js_files(html_text: str, base: str = "") -> list[str]:
    files: set[str] = set()
    for m in _JS_FILE_RE.finditer(html_text or ""):
        files.add(m.group(1))
    return sorted(files)


def extract_secret_candidates(text: str, source: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for stype, pat in _SECRET_PATTERNS:
        for m in pat.finditer(text or ""):
            items.append(
                {
                    "kind": "secret_candidate",
                    "source": source,
                    "secret_type": stype,
                    "masked": mask_secret(m.group(0)),
                }
            )
    return items


def analyze(url: str, js_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    endpoints = extract_endpoints(js_text)
    if endpoints:
        items.append({"kind": "js_intel", "url": url, "endpoints": endpoints})
        for ep in endpoints:
            if ep.startswith("/") or ep.startswith("http"):
                items.append(
                    {"kind": "endpoint", "url": ep, "method": "GET", "endpoint_kind": "api"}
                )
    items.extend(extract_secret_candidates(js_text, url))
    return items


def run(url: str, js_text: str = "") -> list[dict[str, Any]]:
    if not js_text:
        from wth_ai.recon import http as rhttp

        resp = rhttp.fetch(url, method="GET")
        js_text = resp.text if resp.ok else ""
    return analyze(url, js_text)
