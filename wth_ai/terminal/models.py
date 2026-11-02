"""Terminal execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TerminalResult:
    transcript_id: str
    actor: str
    purpose: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    blocked: bool = False
    block_reason: str = ""
    url: str = ""
    method: str = ""
    status_code: int = 0
    #: Ringkasan analisa respons (status/ukuran/sidik isi/header penting). Dipakai
    #: agent untuk memutuskan langkah berikutnya dari sinyal, bukan membaca ulang
    #: seluruh badan respons. Lihat `terminal/analysis.py`.
    analysis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript_id": self.transcript_id,
            "actor": self.actor,
            "purpose": self.purpose,
            "command": self.command,
            "stdout": self.stdout[:8000],
            "stderr": self.stderr[:2000],
            "exit_code": self.exit_code,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "url": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "analysis": self.analysis[:4000],
        }

    def summary(self) -> str:
        if self.blocked:
            return f"[TX:{self.transcript_id}] BLOCKED: {self.block_reason}"
        preview = (self.stdout or self.stderr)[:400]
        return f"[TX:{self.transcript_id}] exit={self.exit_code} {preview}"
