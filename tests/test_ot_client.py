"""Modbus TCP read terhadap server palsu (tanpa perangkat nyata)."""

import socket
import struct
import threading

import pytest

from wth_ai.netguard import EgressDenied, EgressGuard
from wth_ai.ot.client import ModbusError, read_registers
from wth_ai.ot.modbus import DestructiveBlocked, build_write_request

REGISTERS = [11, 22, 33, 44, 55, 66, 77, 88]


class _Server(threading.Thread):
    def __init__(self, *, exception: bool = False, short: bool = False):
        super().__init__(daemon=True)
        self.exception = exception
        self.short = short
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            data = conn.recv(1024)
            _tid, _pid, _len, unit = struct.unpack(">HHHB", data[:7])
            function, address, count = struct.unpack(">BHH", data[7:12])
            if self.short:
                conn.sendall(b"\x00\x01")
                return
            if self.exception:
                pdu = struct.pack(">BB", function | 0x80, 2)
            else:
                payload = b"".join(struct.pack(">H", REGISTERS[i])
                                   for i in range(address, address + count))
                pdu = struct.pack(">BB", function, len(payload)) + payload
            conn.sendall(struct.pack(">HHHB", 1, 0, len(pdu) + 1, unit) + pdu)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.sock.close()


def test_read_holding_registers():
    with _Server() as server:
        result = read_registers("127.0.0.1", port=server.port, address=2, count=4)
    assert result.values == [33, 44, 55, 66]
    assert result.destructive is False or result.to_dict()["destructive"] is False
    assert result.latency_ms >= 0


def test_exception_response_is_reported():
    with _Server(exception=True) as server:
        with pytest.raises(ModbusError) as excinfo:
            read_registers("127.0.0.1", port=server.port, count=1)
    assert "exception" in str(excinfo.value)


def test_truncated_response_is_reported():
    with _Server(short=True) as server:
        with pytest.raises(ModbusError):
            read_registers("127.0.0.1", port=server.port, count=1)


def test_guard_blocks_out_of_scope_host():
    guard = EgressGuard.from_patterns(["10.0.0.0/8"])
    with pytest.raises(EgressDenied):
        read_registers("127.0.0.1", port=502, count=1, guard=guard)


def test_guard_allows_in_scope_host():
    guard = EgressGuard.from_patterns(["127.0.0.1/32"])
    with _Server() as server:
        result = read_registers("127.0.0.1", port=server.port, count=2, guard=guard)
    assert result.values == [11, 22]


def test_write_is_still_blocked_by_module():
    with pytest.raises(DestructiveBlocked):
        build_write_request("127.0.0.1", "holding", 0, value=1)