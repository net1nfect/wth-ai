"""Callback Center (F2) — OOB interaction server milik operator sendiri.

Ringkas alur:
    /oob start            -> listener loopback hidup
    /oob new ssrf-blind   -> token + URL callback
    <kirim URL ke vektor> -> target memanggil balik; hit tercatat
    /oob hits <token>     -> hit ditampilkan + disimpan sebagai transcript [TX:...]

Tool agent yang setara: `oob_create`, `oob_check` (loopback-only; host/port
tidak pernah bisa diubah dari argumen LLM — itu permukaan operator).
"""

from wth_ai.oob.dns import DEFAULT_DNS_HOST, DEFAULT_DNS_PORT, DNSListener, build_query, parse_qname
from wth_ai.oob.server import DEFAULT_HOST, OOBCallbackServer
from wth_ai.oob.service import (
    OOBCallbackService,
    format_hits,
    get_oob_service,
    reset_oob_service,
)
from wth_ai.oob.store import DEFAULT_TTL_MINUTES, OOBStore

__all__ = [
    "DEFAULT_DNS_HOST",
    "DEFAULT_DNS_PORT",
    "DEFAULT_HOST",
    "DEFAULT_TTL_MINUTES",
    "DNSListener",
    "OOBCallbackServer",
    "OOBCallbackService",
    "OOBStore",
    "build_query",
    "format_hits",
    "parse_qname",
    "get_oob_service",
    "reset_oob_service",
]
