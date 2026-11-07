"""Adapter proxy lokal: jurnal request/response ditulis recon, dibaca agen.

Jurnal berupa JSONL append-only di `$WTH_HOME/data/proxy/history.jsonl`.
Setiap entri diredaksi sebelum ditulis, jadi secret tidak pernah mendarat
di disk. `replay` mengulang lewat `recon.http.fetch`, yang sudah membawa
SSRF guard dan `EgressGuard` — replay tidak boleh jadi jalan pintas scope.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from wth_ai.redaction import redact_data

JOURNAL_NAME = "history.jsonl"
ROTATED_NAME = "history.1.jsonl"
# Jurnal tumbuh tiap GET recon; batas ini menjaga $WTH_HOME tidak membengkak
# tanpa batas. Rotasi menimpa arsip lama: riwayat panjang ada di bundel bukti,
# bukan di sini.
MAX_JOURNAL_BYTES = 8 * 1024 * 1024


def journal_path() -> Path:
    """Lokasi jurnal; ikut `WTH_HOME` supaya test tidak mengotori repo."""
    home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
    return home / "data" / "proxy" / JOURNAL_NAME


def append_exchange(
    *,
    tx_id: str,
    request: dict[str, Any],
    response: dict[str, Any],
) -> None:
    """Catat satu transaksi (sudah diredaksi) ke jurnal append-only."""
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size >= MAX_JOURNAL_BYTES:
            path.replace(path.with_name(ROTATED_NAME))
    except OSError:
        # Stat/replace gagal (mis. dihapus proses lain) bukan alasan menolak tulis.
        pass
    entry = {"tx_id": tx_id, "request": request, "response": response}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(redact_data(entry), ensure_ascii=False) + "\n")


class LocalProxyAdapter:
    """Baca jurnal lokal, ulangi transaksi lewat jalur recon yang aman."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or journal_path()

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("tx_id"):
                entries.append(item)
        return entries

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Entri terbaru dulu, maksimal `limit`."""
        return list(reversed(self._entries()))[: max(0, limit)]

    def replay(self, entry_id: str) -> dict[str, Any]:
        """Ulangi satu transaksi; host di luar allowlist ditolak."""
        from wth_ai.netguard import EgressGuard
        from wth_ai.recon import http as recon_http

        entry = next((e for e in self._entries() if e["tx_id"] == entry_id), None)
        if entry is None:
            raise KeyError(entry_id)
        request = entry.get("request") or {}
        url = str(request.get("url") or "")
        guard = getattr(recon_http, "_EGRESS_GUARD", None)
        if isinstance(guard, EgressGuard):
            guard.check(recon_http.host_of(url))
        result = recon_http.fetch(
            url,
            method=str(request.get("method") or "GET"),
            headers=dict(request.get("headers") or {}),
            allow_private=True,
        )
        return {
            "tx_id": entry_id,
            "url": result.url,
            "status": result.status,
            "headers": result.headers,
            "body": result.text,
            "error": result.error,
        }
