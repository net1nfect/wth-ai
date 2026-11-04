"""Gerbang egress: allowlist diperiksa SEBELUM socket dibuat.

Ini lapis kedua setelah ScopeManager. ScopeManager menjawab "apakah target ini
dalam izin", netguard menjawab "apakah koneksi keluar ini boleh terjadi sama
sekali". Setiap penolakan masuk audit log sebagai EGRESS_DENIED supaya bisa
ditunjukkan saat demo.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable

from wth_ai.audit import audit_log


class EgressDenied(RuntimeError):
    """Koneksi keluar ditolak allowlist."""


@dataclass(frozen=True)
class EgressGuard:
    cidrs: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()

    @classmethod
    def from_patterns(cls, patterns: Iterable[str] | None) -> "EgressGuard":
        cidrs: list[str] = []
        hosts: list[str] = []
        for raw in patterns or ():
            text = str(raw).strip().lower()
            if not text:
                continue
            if "/" in text and not text.startswith("*."):
                try:
                    cidrs.append(str(ipaddress.ip_network(text, strict=False)))
                    continue
                except ValueError:
                    pass
            hosts.append(text)
        return cls(tuple(cidrs), tuple(hosts))

    def allows(self, host: str) -> bool:
        target = (host or "").strip().lower().rstrip(".")
        if not target:
            return False
        try:
            ip = ipaddress.ip_address(target)
        except ValueError:
            ip = None
        if ip is not None:
            for cidr in self.cidrs:
                try:
                    if ip in ipaddress.ip_network(cidr, strict=False):
                        return True
                except ValueError:
                    continue
            return False
        for pattern in self.hosts:
            if pattern.startswith("*."):
                if target == pattern[2:] or target.endswith(pattern[1:]):
                    return True
            elif target == pattern:
                return True
        return False

    def check(self, host: str) -> None:
        if self.allows(host):
            return
        audit_log(
            "EGRESS_DENIED",
            host=host,
            allowlist=list(self.cidrs) + list(self.hosts),
        )
        raise EgressDenied(f"egress ditolak: {host} tidak ada di allowlist")