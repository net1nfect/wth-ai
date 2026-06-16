"""OpenAPI / Swagger discovery."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests

OPENAPI_PATHS = [
    "/openapi.json", "/swagger.json", "/api-docs", "/v1/swagger",
    "/swagger/v1/swagger.json", "/api/swagger.json", "/docs/openapi.json",
    "/api/openapi.json", "/swagger-ui.html",
]


class OpenApiDiscovery:
    def __init__(self, *, timeout: int = 10) -> None:
        self.timeout = timeout

    def probe(self, base_url: str) -> dict[str, Any]:
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        base_url = base_url.rstrip("/")
        found: list[dict[str, str]] = []
        endpoints: list[str] = []

        for path in OPENAPI_PATHS:
            url = f"{base_url}{path}"
            try:
                r = requests.get(url, timeout=self.timeout, allow_redirects=True)
                if r.status_code != 200:
                    continue
                ct = r.headers.get("content-type", "")
                if "json" in ct or path.endswith(".json"):
                    found.append({"url": url, "type": "openapi"})
                    try:
                        data = r.json()
                        for p, methods in (data.get("paths") or {}).items():
                            for method in methods:
                                if method in ("get", "post", "put", "patch", "delete"):
                                    endpoints.append(f"{method.upper()} {urljoin(base_url, p)}")
                    except Exception:
                        pass
                elif "html" in ct and "swagger" in r.text.lower():
                    found.append({"url": url, "type": "swagger-ui"})
            except requests.RequestException:
                continue

        return {"specs": found, "endpoints": endpoints[:100]}
