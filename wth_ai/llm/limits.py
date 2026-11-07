"""Batas tool-call: per run, per tool, dan per respons model.

Paritas `TurnToolCallLimiter` Strix (`strix/config/tool_call_limits.py`): satu
respons model bisa memuat ratusan tool call sekaligus, biasanya loop poll yang
ditulis di depan. Strix membatasi jumlah call per respons; di sini batas itu
ditambah anggaran per run dan per tool supaya satu tool yang dipanggil berulang
tidak menghabiskan seluruh konteks.

Semua batas bernilai `<= 0` berarti mati: perilaku lama harus tetap sama.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from wth_ai.audit import audit_log

_IDENTICAL_CALL_THRESHOLD = 3


class ToolCallBudget:
    """Anggaran panggilan tool untuk satu run agen."""

    def __init__(
        self,
        max_total: int = 40,
        max_per_tool: int = 12,
        max_in_response: int = 16,
    ) -> None:
        self.max_total = max_total
        self.max_per_tool = max_per_tool
        self.max_in_response = max_in_response
        self.spent = 0
        self.per_tool: Counter[str] = Counter()
        self._identical: Counter[str] = Counter()
        self._audited = False

    def check(self, tool: str) -> str | None:
        """Kembalikan pesan penolakan, atau `None` bila panggilan boleh jalan."""
        if self.max_total > 0 and self.spent >= self.max_total:
            return self._refuse(
                f"anggaran tool habis: {self.spent}/{self.max_total} panggilan", tool
            )
        if self.max_per_tool > 0 and self.per_tool[tool] >= self.max_per_tool:
            return self._refuse(
                f"tool `{tool}` sudah dipanggil {self.per_tool[tool]}× "
                f"(batas {self.max_per_tool})",
                tool,
            )
        return None

    def spend(self, tool: str) -> None:
        self.spent += 1
        self.per_tool[tool] += 1

    def observe(self, tool: str, args: dict[str, Any]) -> str | None:
        """Deteksi panggilan identik berulang.

        Argumen di-hash dengan urutan kunci distabilkan: model sering mengirim
        dict yang sama dengan urutan berbeda, dan itu bukan langkah baru.
        Refusenya recoverable — model melihat penolakan pada turn berikutnya.
        """
        key = hashlib.sha256(
            f"{tool}:{json.dumps(args, sort_keys=True, default=str)}".encode()
        ).hexdigest()
        self._identical[key] += 1
        count = self._identical[key]
        if count >= _IDENTICAL_CALL_THRESHOLD:
            return self._refuse(
                f"panggilan identik `{tool}` ke-{count}; argumen tidak berubah", tool
            )
        return None

    def _refuse(self, reason: str, tool: str) -> str:
        if not self._audited:
            self._audited = True
            audit_log("tool_budget_exhausted", tool=tool, total=self.spent)
        return f"DITOLAK: {reason}. Lanjutkan dengan langkah lain atau akhiri run."


def filter_response_calls(
    calls: list[dict[str, Any]], *, limit: int = 16
) -> tuple[list[dict[str, Any]], int]:
    """Pertahankan `limit` call pertama dari satu respons; sisanya dibuang.

    Model melihat hasil yang dipertahankan pada turn berikutnya dan bisa
    mempertimbangkan ulang, persis seperti alasan Strix membatasi per respons.
    """
    if limit <= 0 or len(calls) <= limit:
        return list(calls), 0
    return list(calls[:limit]), len(calls) - limit
