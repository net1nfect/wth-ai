"""External tool adapters: argv builders + stdout parsers.

Only a curated set of safe, passive external tools have adapters. Every adapter
builds an argv **list** (never a shell string) and parses line-oriented output
into normalized items. Tools without an adapter are reported as ``manual``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse


@dataclass
class Adapter:
    build_args: Callable[[str], list[str]]
    parse: Callable[[str], list[dict[str, Any]]]


def _lines(out: str) -> list[str]:
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]


# --- parsers ---
def _parse_subdomains(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        host = ln.split()[0].lstrip("*.").lower()
        if "." in host:
            items.append({"kind": "subdomain", "host": host})
    return items


def _parse_urls(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        url = ln.split()[0]
        if url.startswith("http"):
            items.append({"kind": "url", "url": url})
    return items


def _parse_httpx(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        url = ln.split()[0]
        if url.startswith("http"):
            items.append({"kind": "url", "url": url})
            items.append({"kind": "http_probe", "url": url, "status": 0})
    return items


def _parse_dnsx(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        host = ln.split()[0].lower()
        if "." in host:
            items.append({"kind": "subdomain", "host": host})
    return items


def _parse_naabu(out: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ln in _lines(out):
        # host:port
        if ":" in ln:
            host, _, port = ln.partition(":")
            items.append(
                {
                    "kind": "finding",
                    "title": f"Open port {port}",
                    "asset": host,
                    "category": "service",
                    "severity_hint": "info",
                    "detail": f"port {port} open (passive light scan)",
                }
            )
    return items


def _host(target: str) -> str:
    if "://" in target:
        return urlparse(target).hostname or target
    return target.split("/")[0]


# --- registry of adapters ---
ADAPTERS: dict[str, Adapter] = {
    "subfinder": Adapter(lambda t: ["subfinder", "-silent", "-d", _host(t)], _parse_subdomains),
    "assetfinder": Adapter(lambda t: ["assetfinder", "--subs-only", _host(t)], _parse_subdomains),
    "findomain": Adapter(lambda t: ["findomain", "-q", "-t", _host(t)], _parse_subdomains),
    "dnsx": Adapter(lambda t: ["dnsx", "-silent", "-d", _host(t)], _parse_dnsx),
    "httpx": Adapter(lambda t: ["httpx", "-silent", "-u", t], _parse_httpx),
    "httprobe": Adapter(lambda t: ["httprobe"], _parse_urls),
    "waybackurls": Adapter(lambda t: ["waybackurls", _host(t)], _parse_urls),
    "gau": Adapter(lambda t: ["gau", "--subs", _host(t)], _parse_urls),
    "katana": Adapter(lambda t: ["katana", "-silent", "-u", t], _parse_urls),
    "hakrawler": Adapter(lambda t: ["hakrawler", "-u", t], _parse_urls),
    "naabu": Adapter(lambda t: ["naabu", "-silent", "-host", _host(t), "-top-ports", "100"], _parse_naabu),
}


def get_adapter(name: str) -> Adapter | None:
    return ADAPTERS.get(name)
