"""OpenAPI / Postman sebagai target kelas satu.

Kontraknya dibaca lebih dulu, lalu base URL yang dideklarasikan di dalamnya masuk
scope — supaya agen menguji permukaan yang dideklarasikan, bukan menemukannya
dengan crawling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

KIND_OPENAPI = "openapi"
KIND_POSTMAN = "postman"


@dataclass
class ApiSpec:
    kind: str
    endpoints: list[str] = field(default_factory=list)
    base_hosts: list[str] = field(default_factory=list)

    def hosts(self) -> list[str]:
        return [host for host in self.base_hosts if host]


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def load_spec(path: Path) -> ApiSpec:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{path} tidak ada")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} bukan JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path} bukan dokumen OpenAPI/Postman")

    if "openapi" in data or "swagger" in data:
        endpoints = [
            f"{method.upper()} {route}"
            for route, body in (data.get("paths") or {}).items()
            if isinstance(body, dict)
            for method in body
            if method.lower() not in {"parameters", "servers", "summary",
                                      "description"}
        ]
        hosts = sorted({_host(server.get("url", ""))
                        for server in (data.get("servers") or [])
                        if isinstance(server, dict)} - {""})
        if not hosts:
            hosts = sorted({_host(data.get("host", ""))} - {""})
        return ApiSpec(KIND_OPENAPI, endpoints, hosts)

    if "info" in data and "item" in data:
        endpoints: list[str] = []

        def walk(items: list[dict], prefix: str = "") -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "item" in item:
                    walk(item.get("item") or [], prefix + "/" + str(item.get("name", "")))
                elif "request" in item:
                    request = item.get("request") or {}
                    raw = request.get("url") or {}
                    route = raw.get("raw", "") if isinstance(raw, dict) else str(raw)
                    endpoints.append(
                        f"{str(request.get('method', 'GET')).upper()} {route}")

        walk(data.get("item") or [])
        hosts = sorted({_host(item.get("request", {}).get("url", {}).get("raw", ""))
                        for item in _all_requests(data.get("item") or [])} - {""})
        return ApiSpec(KIND_POSTMAN, endpoints, hosts)

    raise ValueError(f"{path} bukan OpenAPI/Swagger maupun Postman collection")


def _all_requests(items: list[dict]) -> list[dict]:
    found: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "item" in item:
            found.extend(_all_requests(item.get("item") or []))
        elif "request" in item:
            found.append(item)
    return found
