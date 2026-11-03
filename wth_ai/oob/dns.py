"""DNS listener OOB (F2) — bukti callback lewat resolusi nama.

Dipakai untuk blind XXE / blind SSRF / RCE yang memicu *lookup* DNS tapi tidak
melakukan koneksi HTTP (mis. `<!ENTITY x SYSTEM "http://<token>.oob/">` yang
gagal sebelum HTTP, atau `nslookup <token>.oob`).

Kenapa parser DNS sendiri (stdlib, tanpa dnslib/dnspython):
* yang kita butuhkan hanya qname + qtype dari satu paket query dan jawaban
  minimal; menambah dependensi untuk itu tidak sepadan;
* jalur tulis hit HARUS sama dengan HTTP (`OOBStore.record_interaction` →
  redaksi kanonik), jadi tidak ada logika kedua yang bisa drift.

Keputusan desain:
* **Default 127.0.0.1:5353** — unprivileged, tidak menyentuh resolver sistem.
  `:53` tersedia lewat argumen eksplisit operator bila diperlukan.
* **Selalu menjawab** (A + TXT), termasuk token tak dikenal: resolver pemanggil
  tidak boleh menggantung, dan jawaban yang berbeda akan jadi oracle.
* **Token diambil dari label pertama** qname: `<token>.apa.saja` → `token`. Ini
  yang memungkinkan domain callback bebas ditentukan operator.
* **Tanpa proxy rekursif**: kita bukan resolver; semua query dicatat dan dijawab
  dari data statis. Tidak ada forward, tidak ada amplifikasi.
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Any

from wth_ai.oob.store import OOBStore

DEFAULT_DNS_HOST = "127.0.0.1"
DEFAULT_DNS_PORT = 5353

# Jawaban statis: A -> 127.0.0.1, TXT -> penanda. Isi sengaja tidak informatif.
_ANSWER_A = socket.inet_aton("127.0.0.1")
_ANSWER_TXT = b"wth-oob"
_TTL = 60

QTYPE_NAMES = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX", 16: "TXT", 28: "AAAA", 33: "SRV", 255: "ANY"}


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        if not label:
            continue
        raw = label.encode("idna") if any(ord(c) > 127 for c in label) else label.encode("ascii")
        if len(raw) > 63:
            raise ValueError(f"label DNS terlalu panjang: {label[:20]!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(name: str, qtype: int = 1, *, qid: int = 0) -> bytes:
    """Bangun paket query DNS (untuk test dan untuk tooling internal)."""
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # RD=1, 1 question
    return header + _encode_name(name) + struct.pack("!HH", qtype, 1)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    pos = offset
    guard = 0
    while True:
        if pos >= len(data):
            raise ValueError("paket DNS terpotong saat membaca nama")
        length = data[pos]
        pos += 1
        if length == 0:
            break
        if length & 0xC0:  # pointer kompresi: tidak kita dukung di question section
            raise ValueError("kompresi nama tidak didukung pada question")
        if pos + length > len(data):
            raise ValueError("label DNS melewati akhir paket")
        labels.append(data[pos : pos + length].decode("ascii", errors="replace"))
        pos += length
        guard += 1
        if guard > 128:
            raise ValueError("nama DNS terlalu banyak label")
    return ".".join(labels), pos


def parse_qname(data: bytes) -> tuple[str, int]:
    """Ambil (qname, qtype) dari paket query. ValueError bila tidak valid."""
    if len(data) < 12:
        raise ValueError("paket DNS terlalu pendek")
    qdcount = struct.unpack("!H", data[4:6])[0]
    if qdcount < 1:
        raise ValueError("tidak ada question pada paket DNS")
    name, pos = _read_name(data, 12)
    if pos + 4 > len(data):
        raise ValueError("qtype/qclass terpotong")
    qtype = struct.unpack("!H", data[pos : pos + 2])[0]
    return name, qtype


def _build_response(query: bytes, qname: str, qtype: int) -> bytes:
    """Jawaban minimal: echo question + satu answer (A/TXT; lainnya -> NOERROR 0 answer)."""
    qid = query[:2]
    flags = struct.pack("!H", 0x8180)  # QR=1, RD=1, RA=1, NOERROR
    question = _encode_name(qname) + struct.pack("!HH", qtype, 1)
    if qtype in (1, 255):
        rdata = _ANSWER_A
        rr_type, rr_class = 1, 1
    elif qtype == 16:
        rdata = bytes([len(_ANSWER_TXT)]) + _ANSWER_TXT
        rr_type, rr_class = 16, 1
    elif qtype == 28:  # AAAA -> ::1
        rdata = socket.inet_pton(socket.AF_INET6, "::1")
        rr_type, rr_class = 28, 1
    else:
        return qid + flags + struct.pack("!HHHH", 1, 0, 0, 0) + question
    answer = _encode_name(qname) + struct.pack("!HHIH", rr_type, rr_class, _TTL, len(rdata)) + rdata
    return qid + flags + struct.pack("!HHHH", 1, 1, 0, 0) + question + answer


class DNSListener:
    """Listener UDP DNS; `port=0` -> port ephemeral (`self.port`)."""

    def __init__(self, store: OOBStore, *, host: str = DEFAULT_DNS_HOST, port: int = 0) -> None:
        self.store = store
        self.host = host
        self._port_pref = int(port or 0)
        self.port = 0
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.queries = 0

    @property
    def running(self) -> bool:
        return self._sock is not None

    def start(self) -> dict[str, Any]:
        if self._sock is not None:
            return self.info()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self._port_pref))
        sock.settimeout(0.5)
        self._sock = sock
        self.port = int(sock.getsockname()[1])
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="wth-oob-dns", daemon=True)
        self._thread.start()
        return self.info()

    def _serve(self) -> None:
        sock = self._sock
        while sock is not None and not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                qname, qtype = parse_qname(data)
            except ValueError:
                continue
            # token = label pertama qname (mendukung <token>.sub.domain)
            token = qname.split(".")[0] if qname else ""
            qtype_name = QTYPE_NAMES.get(qtype, str(qtype))
            self.store.record_interaction(
                token=token,
                method="DNS",
                path=qname,
                query=f"{qtype_name} {qname}",
                remote=addr[0] if addr else "",
                headers={"qtype": str(qtype)},
                body="",
            )
            try:
                sock.sendto(_build_response(data, qname, qtype), addr)
                self.queries += 1
            except OSError:
                continue

    def stop(self) -> None:
        self._stop.set()
        sock, thread = self._sock, self._thread
        self._sock = None
        self._thread = None
        if sock is not None:
            sock.close()
        if thread is not None:
            thread.join(timeout=5)

    def info(self) -> dict[str, Any]:
        return {"running": self.running, "host": self.host, "port": self.port, "queries": self.queries}
