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
    # `network` (default, perilaku lama) atau `source` untuk pemindaian codebase.
    kind: str = "network"
    source_path: str = ""

    def validate(self) -> None:
        if not (self.target or "").strip():
            raise ScopeCardError("target kosong")
        if not self.authorized:
            raise ScopeCardError("authorized harus true — tanpa itu tidak ada engagement")
        if self.kind not in {"network", "source"}:
            raise ScopeCardError(f"kind tidak dikenal: {self.kind}")
        if self.kind == "source":
            self._validate_source()
            return
        if not self.cidr and not self.domains:
            # `wth-ai scope --target H --authorized` adalah bentuk yang
            # didokumentasikan; target tunggal bukan izin yang lebih luas,
            # jadi scope diturunkan dari target alih-alih menolak perintahnya.
            self._derive_scope_from_target()
        for entry in [*self.cidr, *self.domains]:
            if not str(entry).strip():
                raise ScopeCardError("entri scope kosong")

    def _derive_scope_from_target(self) -> None:
        """Turunkan CIDR/domain dari `target` bila operator tidak mengisi apa pun.

        Hanya berjalan ketika `cidr` dan `domains` sama-sama kosong. Nilai
        eksplisit selalu menang, sehingga target di luar CIDR yang dicantumkan
        TIDAK pernah memperluas scope secara diam-diam.
        """
        import ipaddress
        from urllib.parse import urlparse

        raw = self.target.strip()
        parsed = urlparse(raw if "://" in raw else f"//{raw}", scheme="http")
        host = (parsed.hostname or raw.split("/")[0].split(":")[0]).lower().rstrip(".")
        if not host:
            raise ScopeCardError(f"target tidak berisi host yang bisa dipakai: {raw}")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            self.domains = [host]
            return
        self.cidr = [f"{address}/{address.max_prefixlen}"]

    def _validate_source(self) -> None:
        """Target source harus path absolut ke direktori yang benar-benar ada.

        Sengaja tidak dibatasi ke `$HOME`: engagement sah sering berada di
        `/srv`, `/opt`, atau mount kerja. Yang dijaga adalah kejelasan — kartu
        mencatat tepat satu pohon direktori, dan pembaca bundel bisa melihatnya.
        """
        raw = (self.source_path or "").strip()
        if not raw:
            raise ScopeCardError("kind=source butuh source_path")
        path = Path(raw)
        if not path.is_absolute():
            raise ScopeCardError(f"source_path harus absolut: {raw}")
        if not path.is_dir():
            raise ScopeCardError(f"source_path harus direktori yang ada: {raw}")
        forbidden = {"/proc", "/sys", "/dev"}
        if str(path) in forbidden:
            raise ScopeCardError(f"source_path tidak boleh sistem virtual: {raw}")

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