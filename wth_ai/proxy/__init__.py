"""Proxy terstruktur: riwayat request/response untuk agen + replay aman.

Adapter lokal membaca jurnal yang ditulis `recon/http.py`; adapter Caido
opsional hanya aktif bila operator menyetel `WTH_CAIDO_URL`. Semua replay
melewati `EgressGuard` — proxy tidak pernah memperluas allowlist.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class ProxyUnavailable(RuntimeError):
    """Proxy yang diminta tidak tersedia (env kosong, server menolak, dsb)."""


@runtime_checkable
class ProxyAdapter(Protocol):
    """Kontrak minimal: baca riwayat, ulangi satu transaksi."""

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Entri terbaru dulu, maksimal `limit`."""
        ...

    def replay(self, entry_id: str) -> dict[str, Any]:
        """Ulangi satu transaksi lewat jalur request yang sama."""
        ...


__all__ = ["ProxyAdapter", "ProxyUnavailable"]
