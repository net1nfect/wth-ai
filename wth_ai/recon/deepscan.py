"""Deep recon capabilities (Fase 6, flowchart Stage 2 Layers 1-4).

These extend the core builtins with additional *passive / safe-active*
capabilities that materially deepen attack-surface mapping without any
destructive action:

  Layer 1 (passive)        : certificate-transparency, historical archive URLs,
                             third-party service mapping.
  Layer 2 (safe-active)    : security.txt, API/schema discovery, SPA routes.
  Layer 3 (application)    : auth surface, upload surface, websocket channels.
  Layer 4 (cloud / infra)  : CDN / edge / WAF / load-balancer fingerprinting.

Every function takes the shared :class:`BuiltinContext` and returns a list of
normalized items (same contract as the core builtins). They never raise and are
safe to run offline (all network goes through ``recon.http.fetch``, which the
test-suite monkeypatches). Secret values are never emitted in the clear.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from wth_ai.recon import http as rhttp
from wth_ai.recon.tools.builtin import BuiltinContext

# --------------------------------------------------------------------------- #
# Keyword tables
# --------------------------------------------------------------------------- #
_AUTH_HINTS = (
    "login", "log-in", "signin", "sign-in", "auth", "oauth", "openid", "sso",
    "saml", "register", "signup", "sign-up", "logout", "session", "password",
    "reset", "forgot", "mfa", "2fa", "otp", "token", "callback",
)
_UPLOAD_HINTS = ("upload", "import", "attachment", "/file", "files", "media", "avatar", "document")
_SCHEMA_PATHS = (
    "/openapi.json", "/swagger.json", "/swagger/v1/swagger.json", "/v2/api-docs",
    "/v3/api-docs", "/api-docs", "/api/swagger.json", "/.well-known/openapi.json",
    "/swagger-ui.html", "/api/docs", "/redoc",
)
_GRAPHQL_PATHS = ("/graphql", "/api/graphql", "/graphiql", "/v1/graphql")

# CDN / WAF / LB header signatures: header (lowercase) -> (match-substr, technology)
_CDN_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("server", "cloudflare", "cloudflare"),
    ("cf-ray", "", "cloudflare"),
    ("cf-cache-status", "", "cloudflare"),
    ("x-amz-cf-id", "", "aws_cloudfront"),
    ("via", "cloudfront", "aws_cloudfront"),
    ("x-cache", "cloudfront", "aws_cloudfront"),
    ("x-akamai-transformed", "", "akamai"),
    ("server", "akamaighost", "akamai"),
    ("x-served-by", "fastly", "fastly"),
    ("x-fastly-request-id", "", "fastly"),
    ("server", "fastly", "fastly"),
    ("x-sucuri-id", "", "sucuri_waf"),
    ("server", "sucuri", "sucuri_waf"),
    ("x-cdn", "incapsula", "imperva_incapsula"),
    ("x-iinfo", "", "imperva_incapsula"),
    ("server", "awselb", "aws_elb"),
    ("server", "envoy", "envoy_proxy"),
)
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".woff", ".woff2", ".ico")


def _registrable(host: str) -> str:
    parts = (host or "").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _in_scope_host(host: str, base_host: str) -> bool:
    return host == base_host or host.endswith("." + _registrable(base_host))


# --------------------------------------------------------------------------- #
# Layer 1 — passive
# --------------------------------------------------------------------------- #
def ct_logs(ctx: BuiltinContext) -> list[dict]:
    """Certificate-transparency subdomain discovery via crt.sh (passive)."""
    items: list[dict] = []
    url = f"https://crt.sh/?q=%25.{ctx.host}&output=json"
    resp = rhttp.fetch(url, method="GET")
    if not resp.ok or resp.status >= 400 or not resp.text:
        return items
    seen: set[str] = set()
    try:
        data = json.loads(resp.text)
        rows = data if isinstance(data, list) else []
    except (ValueError, TypeError):
        rows = []
    for row in rows[:500]:
        name = str(row.get("name_value", "")) if isinstance(row, dict) else ""
        for cand in re.split(r"[\n,]", name):
            cand = cand.strip().lstrip("*.").lower()
            if cand and "." in cand and _in_scope_host(cand, ctx.host) and cand not in seen:
                seen.add(cand)
                items.append({"kind": "subdomain", "host": cand})
    return items[:200]


def archive_urls(ctx: BuiltinContext) -> list[dict]:
    """Historical URL discovery via the Wayback CDX API (passive)."""
    items: list[dict] = []
    url = (
        f"https://web.archive.org/cdx/search/cdx?url={ctx.host}/*"
        "&output=text&fl=original&collapse=urlkey&limit=100"
    )
    resp = rhttp.fetch(url, method="GET")
    if not resp.ok or resp.status >= 400 or not resp.text:
        return items
    seen: set[str] = set()
    for line in resp.text.splitlines()[:300]:
        u = line.strip()
        if not u.startswith("http"):
            continue
        host = urlparse(u).hostname or ""
        if host and _in_scope_host(host.lower(), ctx.host) and u not in seen:
            seen.add(u)
            items.append({"kind": "url", "url": u})
    return items[:150]


def third_party(ctx: BuiltinContext) -> list[dict]:
    """External (out-of-scope-host) service references from HTML/JS (passive)."""
    items: list[dict] = []
    text = ctx.homepage_html + "\n" + "\n".join(ctx.js_bodies.values())[:200_000]
    hosts: set[str] = set()
    for m in re.finditer(r"""https?://([A-Za-z0-9.\-]+)""", text):
        host = m.group(1).lower().rstrip(".")
        if not host or _in_scope_host(host, ctx.host):
            continue
        if host in hosts:
            continue
        hosts.add(host)
    for host in sorted(hosts)[:60]:
        items.append({"kind": "cloud_candidate", "provider": "third_party", "asset": host})
    return items


# --------------------------------------------------------------------------- #
# Layer 2 — safe-active
# --------------------------------------------------------------------------- #
def security_txt(ctx: BuiltinContext) -> list[dict]:
    items: list[dict] = []
    for path in ("/.well-known/security.txt", "/security.txt"):
        resp = rhttp.fetch(urljoin(ctx.base_url + "/", path.lstrip("/")), method="GET")
        body = resp.text or ""
        looks_like = bool(re.search(r"(?im)^\s*(Contact|Policy|Expires|Encryption)\s*:", body))
        if resp.ok and resp.status < 400 and looks_like:
            items.append({"kind": "url", "url": resp.url})
            items.append({
                "kind": "finding", "title": "security.txt published", "asset": resp.url,
                "category": "info", "severity_hint": "info",
                "detail": "Disclosure policy / contact present.",
            })
            break
    return items


def api_schema(ctx: BuiltinContext) -> list[dict]:
    """Probe well-known API schema / GraphQL endpoints (safe GET only)."""
    items: list[dict] = []
    for path in _SCHEMA_PATHS:
        resp = rhttp.fetch(urljoin(ctx.base_url + "/", path.lstrip("/")), method="GET")
        if not resp.ok or resp.status >= 400 or not resp.text:
            continue
        body = resp.text[:4000].lower()
        if any(k in body for k in ('"openapi"', '"swagger"', '"paths"', "swagger-ui", "redoc")):
            items.append({"kind": "endpoint", "url": resp.url, "method": "GET",
                          "endpoint_kind": "api_schema"})
            items.append({"kind": "finding", "title": "Exposed API schema/docs",
                          "asset": resp.url, "category": "api", "severity_hint": "low",
                          "detail": "Machine-readable API definition is publicly reachable."})
    for path in _GRAPHQL_PATHS:
        resp = rhttp.fetch(urljoin(ctx.base_url + "/", path.lstrip("/")), method="GET")
        if resp.ok and resp.status < 500:
            body = (resp.text or "")[:2000].lower()
            # Only flag when the body actually looks like a GraphQL surface, never
            # on a generic 200 (avoids false positives on SPA catch-all routes).
            if "graphql" in body or "graphiql" in body or '"errors"' in body:
                items.append({"kind": "endpoint", "url": resp.url, "method": "GET",
                              "endpoint_kind": "graphql"})
    return items


def spa_routes(ctx: BuiltinContext) -> list[dict]:
    """Reconstruct SPA client routes from already-fetched JS bundles."""
    items: list[dict] = []
    route_re = re.compile(r"""(?:path|route|to)\s*[:=]\s*["'`](/[A-Za-z0-9_\-/:.]{1,80})["'`]""")
    seen: set[str] = set()
    for body in list(ctx.js_bodies.values())[:12]:
        for m in route_re.finditer(body or ""):
            route = m.group(1)
            if route in seen or route in ("/",):
                continue
            seen.add(route)
            items.append({"kind": "endpoint", "url": route, "method": "GET",
                          "endpoint_kind": "spa_route"})
    return items[:120]


# --------------------------------------------------------------------------- #
# Layer 3 — application
# --------------------------------------------------------------------------- #
def auth_surface(ctx: BuiltinContext) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for url in ctx.urls:
        low = url.lower()
        if any(h in low for h in _AUTH_HINTS) and url not in seen:
            seen.add(url)
            items.append({"kind": "endpoint", "url": url, "method": "GET",
                          "endpoint_kind": "auth"})
    return items[:80]


def upload_surface(ctx: BuiltinContext) -> list[dict]:
    items: list[dict] = []
    html = ctx.homepage_html or ""
    if re.search(r"""<input[^>]+type\s*=\s*["']?file""", html, re.I) or \
       re.search(r"""enctype\s*=\s*["']?multipart/form-data""", html, re.I):
        items.append({"kind": "finding", "title": "File upload form detected",
                      "asset": ctx.base_url, "category": "upload", "severity_hint": "low",
                      "detail": "Multipart/file input present on the homepage."})
    seen: set[str] = set()
    for url in ctx.urls:
        low = url.lower()
        if any(h in low for h in _UPLOAD_HINTS) and url not in seen:
            seen.add(url)
            items.append({"kind": "endpoint", "url": url, "method": "GET",
                          "endpoint_kind": "upload"})
    return items[:60]


def websocket(ctx: BuiltinContext) -> list[dict]:
    items: list[dict] = []
    text = ctx.homepage_html + "\n" + "\n".join(ctx.js_bodies.values())[:200_000]
    seen: set[str] = set()
    for m in re.finditer(r"""(wss?://[A-Za-z0-9._:\-/]+)""", text):
        u = m.group(1)
        if u not in seen:
            seen.add(u)
            items.append({"kind": "endpoint", "url": u, "method": "GET",
                          "endpoint_kind": "websocket"})
    if re.search(r"new\s+WebSocket\s*\(|socket\.io|io\.connect\(", text):
        items.append({"kind": "finding", "title": "Realtime/WebSocket usage detected",
                      "asset": ctx.base_url, "category": "websocket", "severity_hint": "info",
                      "detail": "Client establishes WebSocket / socket.io channels."})
    return items[:60]


# --------------------------------------------------------------------------- #
# Layer 4 — cloud / infrastructure
# --------------------------------------------------------------------------- #
def cdn_waf(ctx: BuiltinContext) -> list[dict]:
    items: list[dict] = []
    hdrs = {k.lower(): str(v) for k, v in (ctx.homepage_headers or {}).items()}
    if not hdrs:
        resp = rhttp.fetch(ctx.base_url, method="GET")
        if resp.ok:
            hdrs = {k.lower(): str(v) for k, v in resp.headers.items()}
    seen: set[str] = set()
    for header, needle, tech in _CDN_SIGNATURES:
        val = hdrs.get(header)
        if val is None:
            continue
        if needle and needle not in val.lower():
            continue
        if tech in seen:
            continue
        seen.add(tech)
        items.append({"kind": "fingerprint", "host": ctx.host, "technology": tech})
    return items


# --------------------------------------------------------------------------- #
# Step registry — name -> (layer, function)
# --------------------------------------------------------------------------- #
DEEP_STEPS: dict[str, callable] = {
    "ct_logs": ct_logs,
    "archive_urls": archive_urls,
    "third_party": third_party,
    "security_txt": security_txt,
    "api_schema": api_schema,
    "spa_routes": spa_routes,
    "auth_surface": auth_surface,
    "upload_surface": upload_surface,
    "websocket": websocket,
    "cdn_waf": cdn_waf,
}


def run_step(name: str, ctx: BuiltinContext) -> list[dict]:
    fn = DEEP_STEPS.get(name)
    if fn is None:
        return []
    try:
        items = fn(ctx) or []
    except Exception:  # noqa: BLE001 — deep steps must never break the pipeline
        return []
    ctx.absorb(items)
    return items
