"""Callback Center (F2) — OOB interaction server milik operator sendiri.

Ringkas alur:
    /oob start            -> listener loopback hidup
    /oob new ssrf-blind   -> token + URL callback
    <kirim URL ke vektor> -> target memanggil balik; hit tercatat
    /oob hits <token>     -> hit ditampilkan + disimpan sebagai transcript [TX:...]

Tool agent yang setara: `oob_create`, `oob_check` (loopback-only; host/port
tidak pernah bisa diubah dari argumen LLM — itu permukaan operator).
"""

from wth_ai.oob.server import DEFAULT_HOST, OOBCallbackServer
from wth_ai.oob.service import (
    OOBCallbackService,
    format_hits,
    get_oob_service,
    reset_oob_service,
)
from wth_ai.oob.store import DEFAULT_TTL_MINUTES, OOBStore

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_TTL_MINUTES",
    "OOBCallbackServer",
    "OOBCallbackService",
    "OOBStore",
    "format_hits",
    "get_oob_service",
    "reset_oob_service",
]
