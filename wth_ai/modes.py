"""Mode agen WTH-AI.

Model: `auto` adalah himpunan penuh kapabilitas. Mode lain adalah **pecahan**
(subset) dari himpunan itu dengan tingkat otonomi lebih rendah. Tidak ada mode
yang punya kapabilitas di luar `auto`.

Dua invarian yang tidak dimiliki mode mana pun, termasuk auto:
  * SCOPE_ENFORCED   — setiap koneksi keluar lewat allowlist CIDR/host
  * KILL_SWITCH      — berhenti <= 2 detik, tanpa konfirmasi

Modul ini sengaja tidak meng-import textual: TUI membacanya, bukan sebaliknya.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Cap(str, Enum):
    """Kapabilitas. Nama sengaja kasar supaya mudah dibaca di matrix."""

    RECON = "recon"            # enumerasi permukaan, tanpa payload
    PROBE = "probe"            # request terkendali, deteksi
    EXPLOIT = "exploit"        # PoC terhadap temuan
    CHAIN = "chain"            # rangkai temuan jadi rantai dampak
    NOVEL = "novel"            # mesin penemuan tanpa CVE
    WRITE = "write"            # tulis laporan/bundel bukti
    SKILL = "skill"            # muat skill eksternal
    MCP = "mcp"                # panggil tool MCP
    MUTATE = "mutate"          # ubah state target (destruktif; butuh flag)


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    glyph: str
    color: str
    autonomy: int                 # 0 diam .. 3 penuh
    caps: frozenset[Cap]
    asks: bool                    # minta konfirmasi sebelum aksi berisiko
    stops_early: bool             # berhenti di temuan pertama
    blurb: str


AUTO_CAPS = frozenset(Cap)

MODES: tuple[Mode, ...] = (
    Mode(
        key="plan", label="plan", glyph="◇", color="#a78bfa", autonomy=0,
        caps=frozenset({Cap.RECON, Cap.PROBE, Cap.WRITE, Cap.SKILL, Cap.MCP}),
        asks=True, stops_early=True,
        blurb="Hanya membaca. Menyusun rencana uji, tanpa PoC.",
    ),
    Mode(
        key="build", label="build", glyph="◆", color="#4ade80", autonomy=1,
        caps=frozenset({Cap.RECON, Cap.PROBE, Cap.EXPLOIT, Cap.WRITE,
                        Cap.SKILL, Cap.MCP}),
        asks=True, stops_early=True,
        blurb="Menjalankan uji dan PoC, dengan konfirmasi per langkah.",
    ),
    Mode(
        key="auto", label="auto", glyph="⚡", color="#f87171", autonomy=3,
        caps=AUTO_CAPS, asks=False, stops_early=False,
        blurb="YOLO: tanpa konfirmasi, tanpa early exit, eskalasi rantai.",
    ),
    # --- pecahan dari auto ---
    Mode(
        key="scout", label="scout", glyph="◎", color="#22d3ee", autonomy=2,
        caps=frozenset({Cap.RECON, Cap.SKILL, Cap.MCP}),
        asks=False, stops_early=False,
        blurb="Pecahan auto: enumerasi permukaan saja, tanpa payload.",
    ),
    Mode(
        key="strike", label="strike", glyph="✦", color="#fbbf24", autonomy=2,
        caps=frozenset({Cap.PROBE, Cap.EXPLOIT, Cap.SKILL, Cap.MCP}),
        asks=False, stops_early=False,
        blurb="Pecahan auto: eksekusi PoC pada temuan yang sudah ada.",
    ),
    Mode(
        key="chain", label="chain", glyph="⛓", color="#a78bfa", autonomy=2,
        caps=frozenset({Cap.EXPLOIT, Cap.CHAIN, Cap.NOVEL, Cap.SKILL, Cap.MCP}),
        asks=False, stops_early=False,
        blurb="Pecahan auto: merangkai temuan, mengejar dampak.",
    ),
    Mode(
        key="scribe", label="scribe", glyph="✎", color="#e5e7eb", autonomy=1,
        caps=frozenset({Cap.WRITE, Cap.MCP}),
        asks=False, stops_early=False,
        blurb="Pecahan auto: laporan, bundel bukti, paket disclosure.",
    ),
)

BY_KEY: dict[str, Mode] = {m.key: m for m in MODES}
#: Mode yang boleh dipakai sebagai pecahan auto (semua selain plan/build).
FRAGMENTS: tuple[str, ...] = tuple(m.key for m in MODES if m.autonomy == 2)


class CapabilityDenied(PermissionError):
    """Dilempar saat mode mencoba kapabilitas di luar himpunannya."""


def check(mode: Mode, cap: Cap) -> bool:
    """True bila mode boleh melakukan `cap`."""
    return cap in mode.caps


def require(mode: Mode, cap: Cap) -> None:
    if not check(mode, cap):
        raise CapabilityDenied(
            f"mode '{mode.key}' tidak mengizinkan '{cap.value}'"
        )


def next_mode(current: str, step: int = 1) -> str:
    keys = [m.key for m in MODES]
    index = keys.index(current) if current in keys else 0
    return keys[(index + step) % len(keys)]


def matrix_rows() -> list[tuple[str, ...]]:
    """Baris tabel untuk /modes: mode × kapabilitas."""
    columns = list(Cap)
    rows: list[tuple[str, ...]] = []
    for mode in MODES:
        cells = ["●" if cap in mode.caps else "·" for cap in columns]
        rows.append((f"{mode.glyph} {mode.label}", f"L{mode.autonomy}",
                     "ya" if mode.asks else "tidak", *cells))
    return rows


def capability_header() -> tuple[str, ...]:
    return ("mode", "otonomi", "tanya", *[cap.value for cap in Cap])