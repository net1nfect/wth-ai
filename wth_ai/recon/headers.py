"""Builtin HTTP header & cookie posture analysis (passive)."""

from __future__ import annotations

from typing import Any

from wth_ai.recon import http as rhttp

SECURITY_HEADERS: tuple[str, ...] = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
)


def analyze(url: str, response: rhttp.HttpResponse | None = None) -> list[dict[str, Any]]:
    """Analyze response headers/cookies -> normalized items.

    If ``response`` is not supplied, performs a GET via the builtin HTTP layer.
    """
    resp = response or rhttp.fetch(url, method="GET")
    if not resp.ok:
        return []

    # case-insensitive header map
    hmap = {k.lower(): v for k, v in resp.headers.items()}
    missing = [h for h in SECURITY_HEADERS if h.lower() not in hmap]
    present = {h: hmap[h.lower()] for h in SECURITY_HEADERS if h.lower() in hmap}

    items: list[dict[str, Any]] = [
        {"kind": "header", "url": resp.url, "missing": missing, "present": present}
    ]

    if missing:
        items.append(
            {
                "kind": "finding",
                "title": f"Missing security headers: {', '.join(missing)}",
                "asset": resp.url,
                "category": "misconfig",
                "severity_hint": "low",
                "detail": "Passive header posture check.",
            }
        )

    for cookie in resp.cookies:
        items.append(
            {
                "kind": "cookie",
                "url": resp.url,
                "name": cookie.get("name", ""),
                "secure": bool(cookie.get("secure", False)),
                "httponly": bool(cookie.get("httponly", False)),
                "samesite": cookie.get("samesite", "") or "",
            }
        )
        if not cookie.get("secure") or not cookie.get("httponly"):
            items.append(
                {
                    "kind": "finding",
                    "title": f"Cookie '{cookie.get('name','')}' weak flags",
                    "asset": resp.url,
                    "category": "misconfig",
                    "severity_hint": "low",
                    "detail": "Cookie missing Secure/HttpOnly (passive observation).",
                }
            )
    return items


def run(url: str) -> list[dict[str, Any]]:
    return analyze(url)
