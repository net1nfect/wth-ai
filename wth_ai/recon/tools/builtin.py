"""Builtin recon tools: pure-Python implementations that always run.

These guarantee a fresh install (no external binaries) can still perform real
recon. Each builtin maps a catalog tool name to a function that returns a list
of normalized items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from wth_ai.recon import cloud, crawler, dns, fingerprint, headers, http as rhttp, js, params


@dataclass
class BuiltinContext:
    """Shared, mutable state passed to builtin tools during a recon run."""

    target: str
    host: str
    base_url: str
    subdomains: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    js_files: list[str] = field(default_factory=list)
    homepage_html: str = ""

    def absorb(self, items: list[dict[str, Any]]) -> None:
        """Fold normalized items back into accumulators for later builtins."""
        for it in items:
            kind = it.get("kind")
            if kind == "url" and it["url"] not in self.urls:
                self.urls.append(it["url"])
            elif kind == "subdomain" and it["host"] not in self.subdomains:
                self.subdomains.append(it["host"])
            elif kind == "js_intel" and it["url"] not in self.js_files:
                self.js_files.append(it["url"])


# --- individual builtins ---
def _builtin_http(ctx: BuiltinContext) -> list[dict[str, Any]]:
    resp = rhttp.fetch(ctx.base_url, method="GET")
    if not resp.ok:
        return []
    ctx.homepage_html = resp.text
    title = ""
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", resp.text or "", re.I | re.S)
    if m:
        title = m.group(1).strip()[:200]
    items: list[dict[str, Any]] = [
        {
            "kind": "http_probe",
            "url": resp.url,
            "status": resp.status,
            "title": title,
            "server": resp.headers.get("Server", resp.headers.get("server", "")),
            "content_type": resp.headers.get("Content-Type", resp.headers.get("content-type", "")),
            "length": len(resp.text or ""),
        },
        {"kind": "url", "url": resp.url},
    ]
    # discover JS files from homepage
    for jf in js.extract_js_files(resp.text):
        absolute = rhttp.normalize_url(jf) if jf.startswith("http") else None
        from urllib.parse import urljoin

        full = absolute or urljoin(resp.url, jf)
        if full not in ctx.js_files:
            ctx.js_files.append(full)
    return items


def _builtin_dns(ctx: BuiltinContext) -> list[dict[str, Any]]:
    return dns.run(ctx.host, subdomains=ctx.subdomains)


def _builtin_headers(ctx: BuiltinContext) -> list[dict[str, Any]]:
    return headers.run(ctx.base_url)


def _builtin_tls(ctx: BuiltinContext) -> list[dict[str, Any]]:
    return tls_run(ctx)


def tls_run(ctx: BuiltinContext) -> list[dict[str, Any]]:
    from wth_ai.recon import tls

    return tls.run(ctx.host)


def _builtin_robots(ctx: BuiltinContext) -> list[dict[str, Any]]:
    from urllib.parse import urljoin

    items: list[dict[str, Any]] = []
    for path in ("robots.txt", "sitemap.xml"):
        resp = rhttp.fetch(urljoin(ctx.base_url + "/", path), method="GET")
        if not resp.ok or resp.status >= 400:
            continue
        items.append({"kind": "url", "url": resp.url})
        import re

        for m in re.finditer(r"(?:Disallow|Allow):\s*(\S+)", resp.text or ""):
            p = m.group(1)
            if p and p != "/":
                items.append({"kind": "url", "url": urljoin(ctx.base_url, p)})
        for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text or ""):
            items.append({"kind": "url", "url": m.group(1)})
    return items


def _builtin_crawler(ctx: BuiltinContext) -> list[dict[str, Any]]:
    return crawler.run(ctx.base_url, max_pages=8)


def _builtin_js(ctx: BuiltinContext) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    targets = ctx.js_files[:10]
    if not targets and ctx.homepage_html:
        items.extend(js.analyze(ctx.base_url, ctx.homepage_html))
    for jf in targets:
        resp = rhttp.fetch(jf, method="GET")
        if resp.ok:
            items.extend(js.analyze(jf, resp.text))
    return items


def _builtin_params(ctx: BuiltinContext) -> list[dict[str, Any]]:
    urls = ctx.urls or [ctx.base_url]
    return params.run(urls)


def _builtin_cloud(ctx: BuiltinContext) -> list[dict[str, Any]]:
    extra = ctx.homepage_html + "\n" + "\n".join(ctx.urls[:200])
    return cloud.run(ctx.host, extra_text=extra)


def _builtin_fingerprint(ctx: BuiltinContext) -> list[dict[str, Any]]:
    return fingerprint.run(ctx.base_url)


# key -> builtin callable
BUILTIN_FUNCS: dict[str, Callable[[BuiltinContext], list[dict[str, Any]]]] = {
    "http": _builtin_http,
    "dns": _builtin_dns,
    "headers": _builtin_headers,
    "tls": _builtin_tls,
    "robots": _builtin_robots,
    "crawler": _builtin_crawler,
    "js": _builtin_js,
    "params": _builtin_params,
    "cloud": _builtin_cloud,
    "fingerprint": _builtin_fingerprint,
}


def run_builtin(key: str, ctx: BuiltinContext) -> list[dict[str, Any]]:
    fn = BUILTIN_FUNCS.get(key)
    if fn is None:
        return []
    items = fn(ctx)
    ctx.absorb(items)
    return items
