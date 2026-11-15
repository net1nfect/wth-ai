"""Mutation catalog: concrete request transformations used to bypass edges.

Every strategy is a pure function over `HttpRequest`. Ranking combines the
target's own measured history (TargetMemory) with WAF/vendor priors, so the
engine tries what has actually worked against this host first.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlparse, urlunparse

from .request import HttpRequest


@dataclass(frozen=True)
class Move:
    strategy: str
    category: str
    description: str
    request: HttpRequest
    score: float
    rationale: str


@dataclass(frozen=True)
class Strategy:
    name: str
    category: str
    description: str
    apply: Callable[[HttpRequest], HttpRequest | None]


def _parts(url: str):
    p = urlparse(url if "://" in url else f"https://{url}")
    return p


def _with(url: str, **changes) -> str:
    p = _parts(url)
    return urlunparse(p._replace(**changes))


def _path(url: str) -> str:
    return _parts(url).path or "/"


def _set_path(url: str, path: str) -> str:
    return _with(url, path=path)


def _set_header(req: HttpRequest, name: str, value: str) -> HttpRequest:
    clone = req.clone()
    for existing in [k for k in clone.headers if k.lower() == name.lower()]:
        clone.headers.pop(existing, None)
    clone.headers[name] = value
    return clone


def _path_case(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    out = []
    flip = True
    for ch in path:
        if ch.isalpha():
            out.append(ch.upper() if flip else ch.lower())
            flip = not flip
        else:
            out.append(ch)
    return HttpRequest(req.method, _set_path(req.url, "".join(out)), dict(req.headers), req.body)


def _trailing_slash(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    new = path.rstrip("/") if path.endswith("/") and len(path) > 1 else path + "/"
    if new == path:
        new = path + "/"
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _double_slash(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    new = "/" + path if not path.startswith("//") else path
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _dot_segment(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    new = re.sub(r"/([^/]+)$", r"/./\\1", path)
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _semicolon(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    if ";" in path:
        new = path.replace(";", "")
    else:
        new = path.rstrip("/") + ";.wth"
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _extension(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    if re.search(r"\.[A-Za-z0-9]{1,6}$", path):
        new = path.rsplit(".", 1)[0] + ".json"
    else:
        new = path + ".json"
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _double_encode(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    new = quote(quote(path, safe="/~!$&'()*+,;=:@"), safe="/~!$&'()*+,;=:@")
    new = new.replace("%25", "%25")  # already double from second pass
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _full_encode(req: HttpRequest) -> HttpRequest:
    path = _path(req.url)
    new = quote(path, safe="/~")
    return HttpRequest(req.method, _set_path(req.url, new), dict(req.headers), req.body)


def _cache_buster(req: HttpRequest) -> HttpRequest:
    p = _parts(req.url)
    token = f"wth{random.randint(1000, 9999)}"
    query = f"{p.query}&{token}=1" if p.query else f"{token}=1"
    return HttpRequest(req.method, _with(req.url, query=query), dict(req.headers), req.body)


def _query_pollution(req: HttpRequest) -> HttpRequest:
    p = _parts(req.url)
    token = f"wthdup=1"
    query = f"{p.query}&{token}" if p.query else token
    return HttpRequest(req.method, _with(req.url, query=query), dict(req.headers), req.body)


def _header(name: str, value_fn: Callable[[HttpRequest], str], category: str, desc: str):
    def apply(req: HttpRequest) -> HttpRequest:
        return _set_header(req, name, value_fn(req))
    return apply, category, desc


def _xff(req: HttpRequest) -> str:
    return "127.0.0.1"


def _real_ip(req: HttpRequest) -> str:
    return "127.0.0.1"


def _forwarded(req: HttpRequest) -> str:
    return "for=127.0.0.1;proto=https;by=127.0.0.1"


def _original_url(req: HttpRequest) -> str:
    p = _parts(req.url)
    return f"{p.path or '/'}{('?' + p.query) if p.query else ''}"


def _host(req: HttpRequest) -> str:
    return _parts(req.url).netloc


def _accept_json(req: HttpRequest) -> str:
    return "application/json, text/plain, */*"


def _accept_xml(req: HttpRequest) -> str:
    return "application/xml, text/xml, */*"


def _json_ct(req: HttpRequest) -> str:
    return "application/json"


def _form_ct(req: HttpRequest) -> str:
    return "application/x-www-form-urlencoded"


def _bot_ua(req: HttpRequest) -> str:
    return "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def _referer(req: HttpRequest) -> str:
    p = _parts(req.url)
    return f"{p.scheme}://{p.netloc}/"


def _no_cache(req: HttpRequest) -> str:
    return "no-cache, no-store"


def _method_override(req: HttpRequest) -> HttpRequest:
    return _set_header(req, "X-HTTP-Method-Override", req.method)


def _method_override_param(req: HttpRequest) -> HttpRequest:
    if req.method.upper() != "GET":
        return req.clone()
    body = "_method=GET"
    return HttpRequest("POST", req.url, {**req.headers, "Content-Type": "application/x-www-form-urlencoded"}, body)


def _method_post_shadow(req: HttpRequest) -> HttpRequest:
    if req.method.upper() != "GET":
        return req.clone()
    return HttpRequest("POST", req.url, {**req.headers, "Content-Type": "application/x-www-form-urlencoded"}, "")


def _build_catalog() -> list[Strategy]:
    items = [
        Strategy("path_case", "path", "flip path case", _path_case),
        Strategy("path_trailing_slash", "path", "add/remove trailing slash", _trailing_slash),
        Strategy("path_double_slash", "path", "double leading slash", _double_slash),
        Strategy("path_dot_segment", "path", "insert /./ segment", _dot_segment),
        Strategy("path_semicolon", "path", "append ;.wth path parameter", _semicolon),
        Strategy("path_extension", "path", "append/swap .json extension", _extension),
        Strategy("path_double_encode", "encoding", "double URL-encode path", _double_encode),
        Strategy("path_full_encode", "encoding", "encode all path metacharacters", _full_encode),
        Strategy("query_cache_buster", "query", "append random query token", _cache_buster),
        Strategy("query_pollution", "query", "append duplicate query parameter", _query_pollution),
        Strategy("method_override_header", "method", "X-HTTP-Method-Override", _method_override),
        Strategy("method_override_param", "method", "POST with _method=GET", _method_override_param),
        Strategy("method_post_shadow", "method", "POST shadow request", _method_post_shadow),
    ]
    header_specs = [
        ("header_xff", "X-Forwarded-For", _xff, "headers", "spoof client IP via XFF"),
        ("header_x_real_ip", "X-Real-IP", _real_ip, "headers", "spoof client IP via X-Real-IP"),
        ("header_forwarded", "Forwarded", _forwarded, "headers", "RFC 7239 Forwarded header"),
        ("header_x_original_url", "X-Original-URL", _original_url, "headers", "route override via X-Original-URL"),
        ("header_x_rewrite_url", "X-Rewrite-URL", _original_url, "headers", "route override via X-Rewrite-URL"),
        ("header_x_host", "X-Host", _host, "headers", "host override via X-Host"),
        ("header_x_forwarded_host", "X-Forwarded-Host", _host, "headers", "host override via X-Forwarded-Host"),
        ("header_accept_json", "Accept", _accept_json, "headers", "ask for JSON"),
        ("header_accept_xml", "Accept", _accept_xml, "headers", "ask for XML"),
        ("header_content_type_json", "Content-Type", _json_ct, "headers", "force JSON content type"),
        ("header_content_type_form", "Content-Type", _form_ct, "headers", "force form content type"),
        ("header_ua_bot", "User-Agent", _bot_ua, "headers", "Googlebot user-agent"),
        ("header_referer", "Referer", _referer, "headers", "same-origin Referer"),
        ("header_no_cache", "Cache-Control", _no_cache, "headers", "disable intermediary cache"),
    ]
    for name, header, value_fn, category, desc in header_specs:
        apply, cat, desc2 = _header(header, value_fn, category, desc)
        items.append(Strategy(name, cat, desc2, apply))
    return items


CATALOG: list[Strategy] = _build_catalog()

_VENDOR_BOOST: dict[str, set[str]] = {
    "cloudflare": {"path_double_encode", "path_case", "header_xff", "header_x_original_url", "method_override_header"},
    "akamai": {"path_case", "path_double_encode", "header_xff", "header_accept_json"},
    "imperva": {"path_double_encode", "header_ua_bot", "header_content_type_json", "path_semicolon"},
    "sucuri": {"path_double_encode", "header_xff", "header_ua_bot"},
    "f5": {"method_override_header", "path_dot_segment", "header_xff", "path_semicolon"},
    "aws-waf": {"header_x_host", "header_xff", "path_double_encode"},
    "azure": {"header_xff", "header_x_original_url", "path_case"},
    "fastly": {"query_cache_buster", "header_xff", "header_ua_bot"},
    "modsecurity": {"path_double_encode", "path_case", "path_full_encode", "header_content_type_json"},
    "barracuda": {"header_xff", "path_double_encode"},
}

_KIND_BOOST: dict[str, set[str]] = {
    "rate_limited": {"header_xff", "query_cache_buster", "header_ua_bot", "header_no_cache"},
    "auth_required": {"header_x_original_url", "header_x_rewrite_url", "method_override_header", "header_xff"},
    "soft_not_found": {"path_extension", "path_trailing_slash", "path_case", "path_dot_segment"},
    "waf": {"path_double_encode", "header_xff", "path_case", "header_ua_bot"},
    "blocked": {"path_double_encode", "method_override_header", "header_x_original_url", "header_xff"},
}


def propose(
    req: HttpRequest,
    *,
    signal_kind: str = "",
    vendor: str = "",
    memory=None,
    limit: int = 8,
    exclude: set[str] | None = None,
) -> list[Move]:
    """Return ranked mutations of `req`, one per distinct resulting request."""
    vendor_boost = _VENDOR_BOOST.get(vendor, set())
    kind_boost = _KIND_BOOST.get(signal_kind, set())
    excluded = exclude or set()
    out: list[Move] = []
    seen = {req.fingerprint()}
    for strat in CATALOG:
        if strat.name in excluded:
            continue
        try:
            mutated = strat.apply(req)
        except Exception:
            continue
        if mutated is None or not mutated.url:
            continue
        fp = mutated.fingerprint()
        if fp in seen:
            continue
        seen.add(fp)
        base = memory.score(strat.name) if memory is not None else 1.0
        boost = 0.0
        rationale = [f"prior {base:.2f}"]
        if strat.name in vendor_boost:
            boost += 1.2
            rationale.append(f"{vendor} fingerprint")
        if strat.name in kind_boost:
            boost += 0.8
            rationale.append(signal_kind)
        score = base + boost
        out.append(Move(
            strategy=strat.name, category=strat.category, description=strat.description,
            request=mutated, score=round(score, 3), rationale=", ".join(rationale),
        ))
    out.sort(key=lambda m: (-m.score, m.strategy))
    return out[:limit]
