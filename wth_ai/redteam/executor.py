"""Kontrak executor: apa yang harus disediakan mesin percobaan.

Loop tidak tahu cara mengirim request. Ia hanya tahu bahwa executor
mengembalikan `AttemptOutcome`, dan bahwa bukti di dalamnya yang menentukan
status — bukan optimisme executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from wth_ai.redteam.models import Finding


@dataclass
class AttemptOutcome:
    executed: bool = False
    #: Bukti request/response tersimpan (path atau [TX:id]).
    request_response: list[str] = field(default_factory=list)
    #: Berapa kali perilaku yang sama teramati (replay).
    reproductions: int = 0
    #: Berapa kontrol negatif yang berperilaku benar.
    controls: int = 0
    #: Kontrol negatif menunjukkan perilaku yang SAMA -> klaim gugur.
    refuted_by_control: bool = False
    #: Bukti berasal dari tool (bukan narasi model).
    tool_backed: bool = False
    detail: str = ""
    #: Langkah tidak bisa dijalankan (bukan "tidak rentan") -> dihitung gagal.
    failed_hard: bool = False
    error: str = ""


class AttemptExecutor(Protocol):
    def attempt(self, finding: Finding, *, attempt_no: int) -> AttemptOutcome:
        ...