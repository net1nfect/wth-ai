"""OT/Modbus recon — hanya baca, dan hanya rencana.

Modul ini **tidak** membuka koneksi. Ia menyusun permintaan baca yang boleh
dijalankan dan menolak semua function code yang menulis. Alasannya praktis: satu
tulis coil ke PLC di lini produksi bisa menghentikan proses fisik, dan itu di
luar batas recon.

Kalau `pymodbus` tersedia, pemanggilan nyata tetap harus lewat executor dengan
`destructive=False`; modul ini hanya menyiapkan argumennya.
"""

from __future__ import annotations

from dataclasses import dataclass

READ_COILS = 1
READ_DISCRETE_INPUTS = 2
READ_HOLDING_REGISTERS = 3
READ_INPUT_REGISTERS = 4
WRITE_COIL = 5
WRITE_REGISTER = 6
WRITE_MULTIPLE_COILS = 15
WRITE_MULTIPLE_REGISTERS = 16

READ_FUNCTIONS = frozenset({READ_COILS, READ_DISCRETE_INPUTS,
                            READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS})
WRITE_FUNCTIONS = frozenset({WRITE_COIL, WRITE_REGISTER, WRITE_MULTIPLE_COILS,
                             WRITE_MULTIPLE_REGISTERS})

KINDS = {
    "coils": READ_COILS,
    "discrete_inputs": READ_DISCRETE_INPUTS,
    "holding": READ_HOLDING_REGISTERS,
    "input": READ_INPUT_REGISTERS,
}

MAX_COUNT = 125          # batas praktis satu PDU Modbus
DEFAULT_PORT = 502


class DestructiveBlocked(PermissionError):
    """Function code menulis — tidak diizinkan di jalur recon."""


@dataclass(frozen=True)
class ReadRequest:
    host: str
    kind: str
    address: int
    count: int = 1
    unit: int = 1
    port: int = DEFAULT_PORT
    timeout: float = 3.0

    @property
    def function_code(self) -> int:
        return KINDS[self.kind]

    @property
    def destructive(self) -> bool:
        return False

    def argv(self) -> list[str]:
        """Argumen yang bisa diteruskan ke executor (argv-only, tanpa shell)."""
        return ["modbus-read", "--host", self.host, "--port", str(self.port),
                "--unit", str(self.unit), "--kind", self.kind,
                "--address", str(self.address), "--count", str(self.count),
                "--timeout", str(self.timeout), "--read-only"]


def build_read_request(host: str, kind: str, address: int, *, count: int = 1,
                       unit: int = 1, port: int = DEFAULT_PORT,
                       timeout: float = 3.0) -> ReadRequest:
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"jenis baca tidak dikenal: {kind!r} "
                         f"(pilih: {', '.join(sorted(KINDS))})")
    if not host:
        raise ValueError("host kosong")
    if not 0 <= int(address):
        raise ValueError("address harus >= 0")
    if not 1 <= int(count) <= MAX_COUNT:
        raise ValueError(f"count harus 1..{MAX_COUNT}")
    if not 1 <= int(unit) <= 247:
        raise ValueError("unit harus 1..247")
    return ReadRequest(host=host, kind=kind, address=int(address),
                       count=int(count), unit=int(unit), port=int(port),
                       timeout=float(timeout))


def build_write_request(*_args, **_kwargs) -> None:
    """Selalu ditolak: menulis ke PLC bukan bagian recon."""
    raise DestructiveBlocked(
        "Modbus write (FC 5/6/15/16) tidak diizinkan — recon hanya baca")


def plan_reads(host: str, *, unit: int = 1, blocks: list[tuple[str, int, int]] | None = None
               ) -> list[ReadRequest]:
    """Rencana baca untuk beberapa blok register, tanpa mengeksekusi apa pun."""
    blocks = blocks or [("holding", 0, 10), ("input", 0, 10)]
    return [build_read_request(host, kind, address, count=count, unit=unit)
            for kind, address, count in blocks]