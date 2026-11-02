"""Analisa & pembanding respons — fondasi pengujian yang adaptif.

Kenapa ini ada: agent hanya melihat `stdout[:3000]` dari setiap request. Untuk kerja
yang bergantung pada perilaku server (business logic, bypass, batas otorisasi),
yang dibutuhkan bukan badan respons mentah, melainkan SINYAL yang bisa dibandingkan
antar-request: status, ukuran, sidik isi, dan header yang menentukan routing/auth.

Semua fungsi di sini murni (tanpa jaringan) supaya bisa diuji langsung, dan yang
dipakai bridge/dispatcher hanya memanggilnya.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

#: Header yang biasanya menjawab "apa yang terjadi" pada uji business logic:
#: routing (Location), auth (WWW-Authenticate, Set-Cookie), tipe isi, dan jejak server.
INTERESTING_HEADERS = (
    "content-type",
    "content-length",
    "location",
    "www-authenticate",
    "set-cookie",
    "server",
    "allow",
    "x-powered-by",
    "x-frame-options",
    "content-security-policy",
    "access-control-allow-origin",
)

#: Penanda bahwa respons "kelihatannya" sebuah halaman/objek yang berbeda, bukan
#: halaman error generik -- dipakai untuk menolak klaim tanpa bukti.
ERROR_MARKERS = (
    "not found", "forbidden", "unauthorized", "access denied", "invalid",
    "error", "exception", "traceback", "stack trace", "internal server error",
)


def body_fingerprint(body: str) -> str:
    """Sidik isi yang stabil untuk dibandingkan antar-request (12 hex pertama)."""
    normalised = re.sub(r"\s+", " ", (body or "").strip())
    return hashlib.sha256(normalised.encode("utf-8", "replace")).hexdigest()[:12]


def pick_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Ambil hanya header yang relevan untuk analisa (huruf kecil, dipotong)."""
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        low = str(key).lower()
        if low in INTERESTING_HEADERS or low.startswith("x-"):
            text = str(value)
            if low == "set-cookie":
                text = f"{len(text.split(','))} cookie" if "," in text else text
            out[low] = text[:120]
    return dict(sorted(out.items()))


def looks_like_error(body: str) -> bool:
    lowered = (body or "").lower()
    return any(marker in lowered for marker in ERROR_MARKERS)


def analyze_response(
    *,
    status: int,
    headers: Mapping[str, Any] | None,
    body: str,
    method: str = "GET",
    url: str = "",
) -> str:
    """Blok analisa ringkas yang dikirim ke agent bersama hasil probe.

    Sengaja berupa baris-baris pendek: agent memutuskan langkah berikutnya dari sini
    (bukan dari membaca ulang seluruh badan respons), jadi yang ditonjolkan adalah
    pembanding antar-request: status, ukuran, sidik isi, dan header penentu.
    """
    interesting = pick_headers(headers or {})
    lines = [
        "[ANALISA RESPONS]",
        f"  status={status} len={len(body or '')} sha={body_fingerprint(body)} "
        f"error_like={'yes' if looks_like_error(body) else 'no'}",
    ]
    if interesting:
        for key, value in interesting.items():
            lines.append(f"  header {key}: {value}")
    else:
        lines.append("  header: (tidak ada header menarik)")
    if method:
        lines.append(f"  request: {method.upper()} {url[:120]}")
    return "\n".join(lines)


def diff_responses(prior: Mapping[str, Any], current: Mapping[str, Any]) -> str:
    """Bandingkan dua transcript (baseline vs mutasi) dan sebutkan yang berubah.

    Inilah yang membuat pengujian bisa adaptif tanpa modul: operator/agent mengubah
    SATU hal, lalu melihat apa yang berubah pada respons. Yang dilaporkan hanya
    perbedaan nyata -- kalau tidak ada yang berubah, itu juga dinyatakan tegas
    supaya tidak dijadikan 'temuan'.
    """
    p_status = int(prior.get("status_code") or 0)
    c_status = int(current.get("status_code") or 0)
    p_body = str(prior.get("stdout") or "")
    c_body = str(current.get("stdout") or "")

    changes: list[str] = []
    if p_status != c_status:
        changes.append(f"status {p_status} -> {c_status}")
    if len(p_body) != len(c_body):
        changes.append(f"panjang {len(p_body)} -> {len(c_body)}")
    if body_fingerprint(p_body) != body_fingerprint(c_body):
        changes.append("isi berbeda")
    if looks_like_error(p_body) != looks_like_error(c_body):
        changes.append(
            "sifat respons berubah: "
            f"error_like {looks_like_error(p_body)} -> {looks_like_error(c_body)}"
        )

    lines = ["[DIFF vs TX]", f"  baseline={prior.get('transcript_id', '?')}"]
    if changes:
        for change in changes:
            lines.append(f"  berubah: {change}")
    else:
        lines.append("  TIDAK ADA PERBEDAAN pada status/ukuran/isi — sinyal ini bukan temuan")
    return "\n".join(lines)


def evidence_pair(result: Any, *, role: str = "adaptive") -> dict[str, Any]:
    """Satu pasang request/response untuk rantai bukti (dipakai /verify & scoring)."""
    return {
        "role": role,
        "method": getattr(result, "method", "") or "GET",
        "url": getattr(result, "url", "") or getattr(result, "command", ""),
        "response_status": getattr(result, "status_code", 0),
        "body_snippet": str(getattr(result, "stdout", "") or "")[:500],
    }


def collect_pairs(results: Iterable[Any]) -> list[dict[str, Any]]:
    return [evidence_pair(r) for r in results if r is not None]
