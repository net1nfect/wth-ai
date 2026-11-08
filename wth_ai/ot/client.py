"""Modbus TCP client minimal (stdlib) — hanya fungsi baca.

Dipakai `wth-ai ot read` untuk membaca register/coil pada perangkat di lab atau
target berizin. Tidak ada dependency: MBAP header + PDU dibangun dengan struct.

Yang tidak ada di sini, dengan sengaja:
  * tulis coil/register (FC 5/6/15/16) — lihat `wth_ai/ot/modbus.py`
  * koneksi otomatis tanpa allowlist — pemanggil wajib memberi `guard`
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from wth_ai.ot.modbus import (DEFAULT_PORT, KINDS, READ_COILS,
                              READ_DISCRETE_INPUTS, build_read_request)

MAX_RESPONSE = 4096


class ModbusError(RuntimeError):
    """Kegagalan protokol atau transport Modbus."""


@dataclass
class ReadResult:
    host: str
    port: int
    unit: int
    kind: str
    address: int
    values: list[int]
    latency_ms: int

    @property
    def destructive(self) -> bool:
        """Selalu False: klien ini tidak punya jalur tulis."""
        return False

    def to_dict(self) -> dict:
        return {
            "host": self.host, "port": self.port, "unit": self.unit,
            "kind": self.kind, "address": self.address,
            "values": self.values, "latency_ms": self.latency_ms,
            "destructive": False,
        }


def _build_pdu(kind: str, address: int, count: int) -> bytes:
    function = KINDS[kind]
    return struct.pack(">BHH", function, address, count)


def _parse_response(data: bytes, kind: str, count: int) -> list[int]:
    if len(data) < 9:
        raise ModbusError(f"respons terlalu pendek ({len(data)} byte)")
    _tid, _pid, length, _unit = struct.unpack(">HHHB", data[:7])
    if length < 3:
        raise ModbusError(f"panjang MBAP tidak masuk akal: {length}")
    function = data[7]
    if function & 0x80:
        code = data[8] if len(data) > 8 else 0
        raise ModbusError(f"exception Modbus kode {code} (FC {function & 0x7F})")
    if function in (READ_COILS, READ_DISCRETE_INPUTS):
        byte_count = data[8]
        payload = data[9:9 + byte_count]
        bits: list[int] = []
        for byte in payload:
            for index in range(8):
                bits.append((byte >> index) & 1)
        return bits[:count]
    byte_count = data[8]
    payload = data[9:9 + byte_count]
    if len(payload) < count * 2:
        raise ModbusError("register tidak lengkap pada respons")
    return [struct.unpack(">H", payload[i * 2:i * 2 + 2])[0] for i in range(count)]


def read_registers(host: str, *, kind: str = "holding", address: int = 0,
                   count: int = 1, unit: int = 1, port: int = DEFAULT_PORT,
                   timeout: float = 3.0, guard=None) -> ReadResult:
    """Baca satu blok. `guard` (EgressGuard) wajib bila host di luar lab lokal."""
    import time

    request = build_read_request(host, kind, address, count=count, unit=unit,
                                 port=port, timeout=timeout)
    if guard is not None:
        guard.check(host)              # EgressDenied naik ke pemanggil

    pdu = _build_pdu(request.kind, request.address, request.count)
    transaction = 1
    mbap = struct.pack(">HHHB", transaction, 0, len(pdu) + 1, request.unit)
    started = time.time()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(mbap + pdu)
        data = sock.recv(MAX_RESPONSE)
    latency = int((time.time() - started) * 1000)
    values = _parse_response(data, request.kind, request.count)
    return ReadResult(host=host, port=port, unit=unit, kind=request.kind,
                      address=address, values=values, latency_ms=latency)


def plan_and_read(requests, *, guard=None) -> list[ReadResult]:
    """Jalankan beberapa ReadRequest berurutan, berhenti pada kegagalan pertama."""
    results: list[ReadResult] = []
    for request in requests:
        results.append(read_registers(
            request.host, kind=request.kind, address=request.address,
            count=request.count, unit=request.unit, port=request.port,
            timeout=request.timeout, guard=guard))
    return results