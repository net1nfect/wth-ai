"""Builtin HTTP layer for the recon platform (non-destructive, GET/HEAD only).

All network access in the builtin recon modules funnels through :func:`fetch`
so tests can monkeypatch a single function to run fully offline.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

DEFAULT_TIMEOUT = 12
DEFAULT_UA = "wth-ai-recon/1.0 (+non-destructive recon)"
MAX_BODY_BYTES = 512_000  # cap captured body to keep things light
MAX_REDIRECTS = 5


def _ip_is_blocked(ip_str: str) -> bool:
    """True if `ip_str` is a non-public address recon must never reach
    (loopback/private/link-local incl. 169.254.169.254 cloud-metadata/
    reserved/multicast/unspecified)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_private_literal(host: str) -> bool:
    """True bila `host` adalah literal IP private/reserved (tanpa DNS)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return _ip_is_blocked(host)


def host_is_blocked(host: str, *, resolve: bool = True, allow_private: bool = False) -> str | None:
    """WU-203 SSRF guard: return a block reason if `host` points at a
    non-public address, else None. IP LITERALS are classified offline
    (always, no network), so an internal literal is blocked even under
    WTH_TEST_MODE. Hostnames are resolved via getaddrinfo only when
    `resolve=True` (callers pass False under test mode to avoid real DNS).
    A hostname that cannot be resolved returns None -- letting the request
    fail naturally downstream, not our job to mask DNS errors as SSRF."""
    if not host:
        return "empty host"
    if allow_private:
        # Opt-in eksplisit: operator MENYEBUT alamat itu sebagai target (lab lokal,
        # staging internal). Tanpa ini, guard tetap memblokir -- dan memang harus,
        # karena guard ini yang mencegah alat berubah jadi proxy jaringan internal.
        return None
    try:
        ipaddress.ip_address(host)  # is it a literal?
        return f"host {host} is a private/reserved address (SSRF guard)" if _ip_is_blocked(host) else None
    except ValueError:
        pass
    if not resolve:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    for info in infos:
        ip_str = info[4][0]
        if _ip_is_blocked(ip_str):
            return f"host {host} resolves to private/reserved address {ip_str} (SSRF guard)"
    return None


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
    """Lengkapi skema. Alamat LOKAL memakai http, host publik memakai https.

    Terukur dari UAT: target `127.0.0.1:42969` menjadi `https://127.0.0.1:42969`,
    sehingga probe TLS terhadap server HTTP polos gagal dan recon melaporkan 0 URL.
    Menurunkan skema untuk host publik tetap TIDAK dilakukan -- hanya alamat
    private/loopback yang memakai http.
    """
    target = target.strip()
    if "://" in target:
        return target
    host = target.split("/")[0].rsplit("@", 1)[-1]
    hostname = host.split(":")[0]
    if is_private_literal(hostname) or hostname in {"localhost"}:
        return f"http://{target}"
    return f"https://{target}"


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
    allow_private: bool = False,
) -> HttpResponse:
    """Perform a safe GET/HEAD request. Never raises — returns an error response.

    Only GET/HEAD are permitted (recon-only, non-destructive).
    """
    method = method.upper()
    if method not in ("GET", "HEAD"):
        return HttpResponse(url=url, error=f"method {method} not allowed (recon-only)")

    url = normalize_url(url)
    import os

    test_mode = bool(os.environ.get("WTH_TEST_MODE"))
    # WU-203 SSRF guard: block private/reserved targets BEFORE any I/O. IP
    # literals are classified offline (so an internal literal is blocked even
    # under test mode); hostnames resolve only outside test mode. Note: recon
    # fetch() only ever hits DISCOVERED external assets -- unlike the vuln
    # SSRF executor which INTENTIONALLY probes 127.0.0.1 as a payload via a
    # different code path (SessionEngine), so this guard does not touch that.
    blocked = host_is_blocked(host_of(url), resolve=not test_mode, allow_private=allow_private)
    if blocked:
        return HttpResponse(url=url, error=blocked)

    # Safety: never perform real network I/O under test mode.
    if test_mode:
        return HttpResponse(url=url, error="test-mode: network disabled")
    req_headers = {"User-Agent": DEFAULT_UA}
    if headers:
        req_headers.update(headers)

    try:
        import time

        import requests

        start = time.time()
        # WU-203: follow redirects MANUALLY so every hop's host is re-checked
        # against the SSRF guard -- a public URL that 30x-redirects to an
        # internal address is a classic SSRF bypass that requests' own
        # allow_redirects=True would follow blindly.
        resp = requests.request(
            method, url, timeout=timeout, headers=req_headers,
            allow_redirects=False, stream=True,
        )
        hops = 0
        while allow_redirects and resp.is_redirect and hops < MAX_REDIRECTS:
            loc = resp.headers.get("Location", "")
            if not loc:
                break
            nxt = urljoin(resp.url, loc)
            hop_block = host_is_blocked(host_of(nxt), resolve=True, allow_private=allow_private)
            if hop_block:
                resp.close()
                return HttpResponse(url=nxt, error=f"redirect blocked: {hop_block}")
            resp.close()
            resp = requests.request(
                method, nxt, timeout=timeout, headers=req_headers,
                allow_redirects=False, stream=True,
            )
            hops += 1
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
