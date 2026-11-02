"""Builtin JavaScript intelligence: endpoint extraction + masked secret clues.

Non-destructive: only parses already-fetched JS/HTML text. Secret *candidates*
are always masked — the full value is never stored, logged, or used.
"""

from __future__ import annotations

import re
from typing import Any

# Relative/absolute path-like strings inside JS.
_ENDPOINT_RE = re.compile(r"""["'`](/[A-Za-z0-9_\-/.${}?=&:%]{2,}|https?://[^"'`\s]{4,})["'`]""")
_JS_FILE_RE = re.compile(r"""["'`]([^"'`\s]+?\.js(?:\?[^"'`\s]*)?)["'`]""")

# A template literal like `/api/users/${id}/posts` is a real route, but the
# `${...}` is a runtime value. Keep one placeholder per expression so the path
# stays recognisable and countable instead of being dropped or split in two.
_TEMPLATE_EXPR_RE = re.compile(r"\$\{[^}]*\}")

# Secret-shaped tokens. We only record the *type* + a masked preview.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}")),
    ("generic_api_key", re.compile(r"(?i)(?:api[_-]?key|secret|token)[\"'`]?\s*[:=]\s*[\"'`][A-Za-z0-9_\-]{16,}[\"'`]")),
)

_NOISE = ("/", "//", "http://", "https://")

_ASSET_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".woff", ".woff2", ".ico", ".map")

# Junk that appears INSIDE javascript bundles and is not an endpoint of the
# target: path traversal/self segments (`/./`), empty segments (`//`),
# percent-encoded whitespace (`%20`), raw whitespace/markup and an
# UN-normalised `${...}` expression.
_JUNK_IN_PATH = re.compile(r"(//|/\.{1,2}(?:/|$)|%(?:20|09|0a|0d)|[\s<>]|\$\{)", re.I)
_ASCII_ONLY = re.compile(r"^[\x20-\x7e]+$")

_DOCS_RE = re.compile(r"(swagger|openapi|api-?docs|redoc|/docs?(?:/|$))", re.I)
_GRAPHQL_RE = re.compile(r"(graphql|/gql(?:/|$))", re.I)
_API_RE = re.compile(r"(^/api(?:/|$)|/api/|/v[1-9]/|/rest/)", re.I)


def mask_secret(value: str) -> str:
    """Mask a secret-like value, keeping only a short prefix hint."""
    value = value.strip().strip("\"'`")
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-2:]}" if len(value) > 12 else value[:3] + "***"


def _host_of(value: str) -> str:
    match = re.match(r"https?://([^/\s:]+)", value or "")
    return match.group(1).lower() if match else ""


def _registrable(host: str) -> str:
    """Last two labels — groups `a.example.com` with `example.com`.

    Deliberately NOT a public-suffix implementation: this filter is a
    heuristic and pulling in PSL data would be a dependency for it. The known
    limitation is that multi-label suffixes (`example.co.uk`) compare one label
    too shallow, so a sibling registrable is treated as same-site.
    """
    labels = [p for p in (host or "").split(".") if p]
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or "")


def _same_site(candidate_host: str, base_host: str) -> bool:
    if not candidate_host or not base_host:
        return False
    if candidate_host == base_host:
        return True
    return _registrable(candidate_host) == _registrable(base_host)


def _path_of(value: str) -> str:
    if value.startswith(("http://", "https://")):
        rest = value.split("://", 1)[1]
        slash = rest.find("/")
        return rest[slash:] if slash != -1 else "/"
    return value


def _is_plausible_endpoint(candidate: str, base_host: str) -> bool:
    """Is `candidate`, found inside a JS bundle, really an endpoint of the target?

    Measured on a real engagement (chat.heroflow.my.id): without this check the
    extractor reported 21 "endpoints", of which the top-scoring ones were
    `http://www.w3.org/2000/svg` (an XML namespace), `https://github.com/
    zloirock/core-js` (a library homepage), `/./`, `/a/b`, `/a/i` (test
    fixtures) and `https://тест`. All of them were labelled `kind="api"` and
    therefore scored as `exposed-api`, dominating the priority list.
    """
    if not candidate or not _ASCII_ONLY.match(candidate):
        return False  # `https://тест`, control characters
    path = _path_of(candidate)
    if _JUNK_IN_PATH.search(path):
        return False
    if not path.strip("/"):
        return False
    if candidate.startswith(("http://", "https://")):
        # A URL only belongs to the target if it is the same site. Scope filter
        # is skipped when the caller supplies no base host.
        if not base_host:
            return True
        return _same_site(_host_of(candidate), base_host)
    return True


def classify_endpoint(candidate: str) -> str:
    """Classify an extracted path instead of calling everything `api`.

    `analyze()` used to stamp `endpoint_kind="api"` on every extracted string,
    and `scoring.py` gives `exposed-api` +3 to exactly that kind — so plain page
    paths were promoted as if they were exposed API surfaces.
    """
    if _GRAPHQL_RE.search(candidate):
        return "graphql"
    if _DOCS_RE.search(candidate):
        return "docs"
    if _API_RE.search(candidate):
        return "api"
    return "web"


def extract_endpoints(js_text: str, *, base_host: str = "") -> list[str]:
    """Paths/URLs found in a JS bundle.

    `base_host` enables the same-site filter for absolute URLs; pass the host
    the bundle was served from. Omitting it keeps absolute URLs unfiltered
    (previous behaviour) for callers that have no target context.

    Template literals are normalised (`/api/users/${id}/posts` ->
    `/api/users/{param}/posts`) instead of being discarded: the earlier regex
    excluded `$`/`{}`, so the single most common way a modern bundle carries a
    route was invisible to the extractor.
    """
    found: set[str] = set()
    for m in _ENDPOINT_RE.finditer(js_text or ""):
        ep = _TEMPLATE_EXPR_RE.sub("{param}", m.group(1))
        if ep in _NOISE or ep.endswith(_ASSET_EXTS):
            continue
        if not _is_plausible_endpoint(ep, base_host):
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
    # The bundle's own host is the scope reference for absolute URLs found
    # inside it: a `github.com/...` or `w3.org/...` string inside a library is
    # not an endpoint of the target.
    base_host = _host_of(url)
    endpoints = extract_endpoints(js_text, base_host=base_host)
    if endpoints:
        items.append({"kind": "js_intel", "url": url, "endpoints": endpoints})
        for ep in endpoints:
            if ep.startswith("/") or ep.startswith("http"):
                items.append(
                    {
                        "kind": "endpoint",
                        "url": ep,
                        "method": "GET",
                        "endpoint_kind": classify_endpoint(ep),
                    }
                )
    items.extend(extract_secret_candidates(js_text, url))
    return items


def run(url: str, js_text: str = "") -> list[dict[str, Any]]:
    if not js_text:
        from wth_ai.recon import http as rhttp

        resp = rhttp.fetch(url, method="GET")
        js_text = resp.text if resp.ok else ""
    return analyze(url, js_text)
