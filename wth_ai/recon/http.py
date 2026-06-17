"""Builtin HTTP layer for the recon platform (non-destructive, GET/HEAD only).

All network access in the builtin recon modules funnels through :func:`fetch`
so tests can monkeypatch a single function to run fully offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 12
DEFAULT_UA = "wth-ai-recon/1.0 (+non-destructive recon)"
MAX_BODY_BYTES = 512_000  # cap captured body to keep things light


@dataclass
class HttpResponse:
    url: str
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    elapsed_ms: int = 0
    error: str | None = None
    cookies: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.status > 0


def normalize_url(target: str) -> str:
    target = target.strip()
    if "://" not in target:
        return f"https://{target}"
    return target


def host_of(target: str) -> str:
    t = normalize_url(target)
    return (urlparse(t).hostname or "").lower().rstrip(".")


def fetch(
    url: str,
    *,
    method: str = "GET",
    timeout: int = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    allow_redirects: bool = True,
) -> HttpResponse:
    """Perform a safe GET/HEAD request. Never raises — returns an error response.

    Only GET/HEAD are permitted (recon-only, non-destructive).
    """
    method = method.upper()
    if method not in ("GET", "HEAD"):
        return HttpResponse(url=url, error=f"method {method} not allowed (recon-only)")

    url = normalize_url(url)
    # Safety: never perform real network I/O under test mode.
    import os

    if os.environ.get("WTH_TEST_MODE"):
        return HttpResponse(url=url, error="test-mode: network disabled")
    req_headers = {"User-Agent": DEFAULT_UA}
    if headers:
        req_headers.update(headers)

    try:
        import time

        import requests

        start = time.time()
        resp = requests.request(
            method,
            url,
            timeout=timeout,
            headers=req_headers,
            allow_redirects=allow_redirects,
            stream=True,
        )
        body = ""
        if method == "GET":
            raw = resp.raw.read(MAX_BODY_BYTES, decode_content=True) if resp.raw else b""
            body = raw.decode(resp.encoding or "utf-8", errors="replace") if raw else ""
        elapsed = int((time.time() - start) * 1000)
        cookies = [
            {
                "name": c.name,
                "secure": bool(c.secure),
                "httponly": bool(c._rest.get("HttpOnly", False)) if hasattr(c, "_rest") else False,
                "samesite": (c._rest.get("SameSite", "") if hasattr(c, "_rest") else ""),
            }
            for c in resp.cookies
        ]
        return HttpResponse(
            url=url,
            status=resp.status_code,
            headers={k: v for k, v in resp.headers.items()},
            text=body,
            elapsed_ms=elapsed,
            cookies=cookies,
        )
    except Exception as exc:  # noqa: BLE001 — network failures are expected
        return HttpResponse(url=url, error=str(exc))
