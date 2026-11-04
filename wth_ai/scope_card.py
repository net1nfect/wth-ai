"""Scope card — rekaman izin yang bisa diperiksa ulang.

Ditulis SEBELUM aksi pertama dan disertakan di bundel bukti. Tujuannya bukan
birokrasi: tanpa rekaman, laporan tidak bisa menjawab "atas izin siapa ini
dijalankan", dan pembaca harus mempercayai kata operator.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class ScopeCardError(ValueError):
    """Scope card tidak lengkap atau tidak jujur."""


@dataclass
class ScopeCard:
    target: str
    cidr: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    authorized: bool = False
    mode: str = "plan"
    destructive: bool = False
    notes: str = ""
    endpoints: list[str] = field(default_factory=list)
    created_at: str = ""

    def validate(self) -> None:
        if not (self.target or "").strip():
            raise ScopeCardError("target kosong")
        if not self.authorized:
            raise ScopeCardError("authorized harus true — tanpa itu tidak ada engagement")
        if not self.cidr and not self.domains:
            raise ScopeCardError("butuh minimal satu CIDR atau domain")
        for entry in [*self.cidr, *self.domains]:
            if not str(entry).strip():
                raise ScopeCardError("entri scope kosong")

    def to_dict(self) -> dict:
        return asdict(self)

    def write(self, path: Path) -> Path:
        self.validate()
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)  # atomik: tidak pernah ada file separuh jadi
        return path

    @classmethod
    def load(cls, path: Path) -> "ScopeCard":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        card = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        card.validate()
        return card