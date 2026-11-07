"""Compaction konteks yang tidak membuang referensi bukti.

Paritas Strix (`strix/llm/compaction.py`): ambang minimal item, potong hasil tool
head+tail, anggaran = jendela konteks dikurangi ruang cadangan. Bedanya, di sini
ringkasan dilakukan deterministik tanpa memanggil model — satu run tidak boleh
mengubah perilaku hanya karena compaction.

Aturan bukti: pesan yang memuat `[TX:...]` atau blok `evidence` tidak pernah
dibuang. Isinya dipotong ke `EVIDENCE_MAX_CHARS` dan diberi penanda bahwa bukti
utuh ada di bundel, supaya pembaca transkrip tidak menyimpulkan bukti hilang.
"""

from __future__ import annotations

from typing import Any

from wth_ai.llm.model_catalog import context_length

TOOL_OUTPUT_MAX_CHARS = 2_000
EVIDENCE_MAX_CHARS = 4_000
MIN_ITEMS_TO_COMPACT = 6
_FALLBACK_WINDOW = 32_000
_RESERVED_TOKENS = 4_000
_MIN_BUDGET_TOKENS = 4_000
_EVIDENCE_MARKERS = ("[TX:", "evidence")
_EVIDENCE_NOTE = "\n…bukti utuh di bundel"
_TRUNCATION_MARK = "\n…dipotong…\n"


def compact_messages(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    keep_last: int = 6,
) -> tuple[list[dict[str, Any]], int]:
    """Ringkas pesan tengah; kembalikan pesan baru dan jumlah yang diringkas."""
    if len(messages) < MIN_ITEMS_TO_COMPACT:
        return list(messages), 0

    window = context_length(model) or _FALLBACK_WINDOW
    budget_bytes = max(_MIN_BUDGET_TOKENS, window - _RESERVED_TOKENS)
    if _total_bytes(messages) <= budget_bytes:
        return list(messages), 0

    head_end = 1 if messages and messages[0].get("role") == "system" else 0
    tail_start = max(head_end, len(messages) - keep_last)

    out: list[dict[str, Any]] = [dict(m) for m in messages[:head_end]]
    summarized = 0
    for message in messages[head_end:tail_start]:
        role = str(message.get("role", ""))
        text = str(message.get("content", ""))
        if _carries_evidence(text):
            out.append({**message, "content": _cut(text, EVIDENCE_MAX_CHARS) + _EVIDENCE_NOTE})
            summarized += 1
            continue
        if role == "tool":
            # Hasil tool adalah observasi, bukan percakapan: dipotong, bukan dibuang.
            out.append({**message, "content": _cut(text, TOOL_OUTPUT_MAX_CHARS)})
            summarized += 1
            continue
        summarized += 1

    out.extend(dict(m) for m in messages[tail_start:])
    return out, summarized


def _carries_evidence(text: str) -> bool:
    return any(marker in text for marker in _EVIDENCE_MARKERS)


def _cut(text: str, limit: int) -> str:
    """Potong head+tail: awal dan akhir dipertahankan, tengah dibuang."""
    if len(text) <= limit:
        return text
    keep = limit - len(_TRUNCATION_MARK)
    head = keep * 3 // 5
    tail = keep - head
    return f"{text[:head]}{_TRUNCATION_MARK}{text[-tail:]}"


def _total_bytes(messages: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content", "")).encode()) for m in messages)
