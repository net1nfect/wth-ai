"""Passive API adapters for recon intel tools."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.parse import quote

import requests

from wth_ai.vault import SecretVault


def _get_key(ref: str | None) -> str | None:
    if not ref:
        return None
    env_key = ref.upper().replace("-", "_")
    val = os.environ.get(env_key)
    if val:
        return val
    try:
        return SecretVault().get(ref)
    except Exception:
        return None


def _safe_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> dict[str, Any] | list[Any] | None:
    if os.environ.get("WTH_TEST_MODE") == "1":
        return None
    try:
        resp = requests.get(url, headers=headers or {}, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None


def crtsh_api(target: str) -> list[dict[str, Any]]:
    host = target.replace("https://", "").replace("http://", "").split("/")[0]
    data = _safe_get(f"https://crt.sh/?q={quote('%.' + host)}&output=json")
    items: list[dict[str, Any]] = []
    if not isinstance(data, list):
        return items
    seen: set[str] = set()
    for row in data[:200]:
        name = (row.get("name_value") or "").split("\n")[0].strip().lower()
        if name and name not in seen and "." in name:
            seen.add(name)
            items.append({"kind": "subdomain", "host": name})
    return items


def shodan_api(target: str, api_key: str) -> list[dict[str, Any]]:
    host = target.replace("https://", "").replace("http://", "").split("/")[0]
    data = _safe_get(f"https://api.shodan.io/dns/domain/{quote(host)}?key={api_key}")
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    for sub in data.get("subdomains", [])[:50]:
        fqdn = f"{sub}.{host}".lower()
        items.append({"kind": "subdomain", "host": fqdn})
    return items


def virustotal_domain(target: str, api_key: str) -> list[dict[str, Any]]:
    host = target.replace("https://", "").replace("http://", "").split("/")[0]
    data = _safe_get(
        f"https://www.virustotal.com/api/v3/domains/{quote(host)}",
        headers={"x-apikey": api_key},
    )
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    attrs = data.get("data", {}).get("attributes", {})
    for sub in (attrs.get("subdomains") or [])[:30]:
        items.append({"kind": "subdomain", "host": str(sub).lower()})
    return items


def urlscan_search(target: str, api_key: str | None = None) -> list[dict[str, Any]]:
    host = target.replace("https://", "").replace("http://", "").split("/")[0]
    headers = {"API-Key": api_key} if api_key else {}
    data = _safe_get(
        f"https://urlscan.io/api/v1/search/?q=domain:{quote(host)}",
        headers=headers,
    )
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    for row in data.get("results", [])[:30]:
        url = (row.get("page", {}) or {}).get("url", "")
        if url.startswith("http"):
            items.append({"kind": "url", "url": url})
    return items


def securitytrails_domain(target: str, api_key: str) -> list[dict[str, Any]]:
    host = target.replace("https://", "").replace("http://", "").split("/")[0]
    data = _safe_get(
        f"https://api.securitytrails.com/v1/domain/{quote(host)}/subdomains",
        headers={"APIKEY": api_key},
    )
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    for sub in data.get("subdomains", [])[:50]:
        items.append({"kind": "subdomain", "host": f"{sub}.{host}".lower()})
    return items


ApiAdapter = Callable[[str], list[dict[str, Any]]]

API_ADAPTERS: dict[str, tuple[str | None, ApiAdapter]] = {
    "crtsh-api": (None, crtsh_api),
    "crtsh": (None, crtsh_api),
    "shodan": ("shodan_api_key", lambda t: shodan_api(t, _get_key("shodan_api_key") or "")),
    "virustotal-domain": ("virustotal_api_key", lambda t: virustotal_domain(t, _get_key("virustotal_api_key") or "")),
    "urlscan-search": ("urlscan_api_key", lambda t: urlscan_search(t, _get_key("urlscan_api_key"))),
    "urlscan-cli": ("urlscan_api_key", lambda t: urlscan_search(t, _get_key("urlscan_api_key"))),
    "securitytrails": ("securitytrails_api_key", lambda t: securitytrails_domain(t, _get_key("securitytrails_api_key") or "")),
    "securitytrails-cli": ("securitytrails_api_key", lambda t: securitytrails_domain(t, _get_key("securitytrails_api_key") or "")),
}


def run_api_adapter(name: str, target: str) -> tuple[list[dict[str, Any]], str | None]:
    """Run API adapter if key available. Returns (items, error)."""
    spec = API_ADAPTERS.get(name)
    if spec is None:
        return [], "no api adapter"
    key_ref, fn = spec
    if key_ref and not _get_key(key_ref):
        return [], "api key missing"
    try:
        return fn(target), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)[:200]
