"""Adapter Caido opsional: baca riwayat dari API Caido lewat HTTP biasa.

Tidak ada dependency baru: Caido diekspos lewat GraphQL, tapi adapter ini
hanya butuh satu query `requests` dan `urllib`. Aktif hanya bila operator
menyetel `WTH_CAIDO_URL`; tanpa itu `ProxyUnavailable`, bukan diam-diam
memakai jurnal lokal. Host Caido tetap harus lolos `EgressGuard`.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from wth_ai.proxy import ProxyUnavailable

HISTORY_QUERY = """
query History($limit: Int!) {
  requests(first: $limit) {
    edges {
      node {
        id
        method
        url
        status
        response { statusCode body }
      }
    }
  }
}
"""


class CaidoAdapter:
    """Baca riwayat + replay dari instance Caido milik operator."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("WTH_CAIDO_URL") or "").strip().rstrip("/")
        self.token = (token or os.environ.get("WTH_CAIDO_TOKEN") or "").strip()

    def _require(self) -> None:
        if not self.base_url:
            raise ProxyUnavailable("WTH_CAIDO_URL belum diset; adapter Caido tidak aktif")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https"):
            raise ProxyUnavailable(f"skema WTH_CAIDO_URL tidak didukung: {parsed.scheme or '(kosong)'}")
        host = parsed.hostname or ""
        from wth_ai.netguard import EgressGuard
        from wth_ai.recon import http as recon_http

        guard = getattr(recon_http, "_EGRESS_GUARD", None)
        if isinstance(guard, EgressGuard):
            guard.check(host)

    def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self._require()
        payload = json.dumps({"query": query, "variables": variables}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"{self.base_url}/graphql", data=payload, headers=headers, method="POST")
        try:
            # URL berasal dari operator dan host-nya sudah lewat EgressGuard.
            with urlopen(request, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProxyUnavailable(f"Caido tidak bisa dihubungi: {exc}") from exc

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        data = self._query(HISTORY_QUERY, {"limit": max(1, limit)})
        edges = (((data or {}).get("data") or {}).get("requests") or {}).get("edges") or []
        entries: list[dict[str, Any]] = []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            if not node.get("id"):
                continue
            response = node.get("response") or {}
            status = node.get("status")
            if not isinstance(status, int):
                status = response.get("statusCode") or 0
            entries.append({
                "tx_id": str(node["id"]),
                "request": {
                    "method": str(node.get("method") or "GET"),
                    "url": str(node.get("url") or ""),
                    "headers": {},
                },
                "response": {
                    "status": int(status) if str(status).isdigit() else 0,
                    "body": str(response.get("body") or ""),
                },
            })
        return entries[: max(0, limit)]

    def replay(self, entry_id: str) -> dict[str, Any]:
        """Replay lewat recon: Caido hanya menyuplai riwayat, bukan izin."""
        entry = next((e for e in self.history(limit=500) if e["tx_id"] == entry_id), None)
        if entry is None:
            raise KeyError(entry_id)
        url = entry["request"]["url"]
        from wth_ai.recon import http as recon_http

        return {
            "tx_id": entry_id,
            "url": url,
            "status": 0,
            "headers": {},
            "body": "",
            "error": recon_http.fetch(url, method=entry["request"]["method"]).error,
        }
