"""HTTP listener callback OOB — endpoint yang kita MILIKI sendiri (F2).

Kenapa stdlib, bukan framework:
* ini listener kecil yang menerima SEMUA metode apa adanya dan hanya mencatat;
  tidak ada routing, parsing, atau plugin — kompleksitas ekstra hanya menambah
  permukaan serangan tanpa manfaat.
* default bind LOOPBACK (`127.0.0.1`). Membuka ke jaringan adalah keputusan
  eksplisit operator (`/oob start --host <ip-lan>`), dan TIDAK bisa dipicu
  lewat argumen tool LLM (lihat `supervisor_dispatch._dispatch_oob_create`).

Kontrak ke arah pemanggil (target):
* Selalu balas 200 teks kecil — termasuk untuk token tak dikenal. Jawaban yang
  beda akan memberi tahu pemanggil apakah token valid (oracle).
* Tidak ada kode yang dieksekusi; body hanya dibaca sampai cap, didekode, lalu
  dicatat lewat `OOBStore.record_interaction` (yang menerapkan redaksi).
"""

from __future__ import annotations

import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from wth_ai.oob.store import MAX_BODY_STORED, OOBStore

DEFAULT_HOST = "127.0.0.1"
_RESPONSE = b"wth-ai oob: recorded\n"


class _OOBRequestHandler(BaseHTTPRequestHandler):
    server_version = "wth-oob/1.0"
    sys_version = ""

    def _record(self, body: str = "") -> None:
        store: OOBStore | None = getattr(self.server, "oob_store", None)
        if store is None:
            return
        parsed = urlsplit(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        token = parts[1] if len(parts) >= 2 and parts[0] == "c" else ""
        remote = self.client_address[0] if self.client_address else ""
        store.record_interaction(
            token=token,
            method=self.command or "",
            path=parsed.path,
            query=parsed.query,
            remote=remote,
            headers=dict(self.headers.items()) if self.headers else {},
            body=body,
        )

    def _read_body(self) -> str:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return ""
        try:
            raw = self.rfile.read(min(length, MAX_BODY_STORED))
        except OSError:
            return ""
        return raw.decode("utf-8", errors="replace")

    def _respond(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(_RESPONSE)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(_RESPONSE)
            except OSError:
                pass

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        self._respond()

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        self._respond()

    def do_POST(self) -> None:  # noqa: N802
        self._record(self._read_body())
        self._respond()

    def do_PUT(self) -> None:  # noqa: N802
        self._record(self._read_body())
        self._respond()

    def do_PATCH(self) -> None:  # noqa: N802
        self._record(self._read_body())
        self._respond()

    def do_DELETE(self) -> None:  # noqa: N802
        self._record()
        self._respond()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._record()
        self._respond()

    def log_message(self, *args: Any) -> None:
        """Bisukan log default (stderr) — hit sudah tercatat di store."""
        return


class OOBCallbackServer:
    """Listener callback; `port=0` -> port ephemeral (`server_address[1]`)."""

    def __init__(self, store: OOBStore, *, host: str = DEFAULT_HOST, port: int = 0) -> None:
        self.store = store
        self.host = host
        self._port_pref = int(port or 0)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> dict[str, Any]:
        if self._httpd is not None:
            return self.info()
        httpd = ThreadingHTTPServer((self.host, self._port_pref), _OOBRequestHandler)
        httpd.daemon_threads = True
        httpd.oob_store = self.store  # type: ignore[attr-defined]
        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        self._thread = threading.Thread(target=httpd.serve_forever, name="wth-oob", daemon=True)
        self._thread.start()
        return self.info()

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5)

    @property
    def url_base(self) -> str:
        # Host wildcard (0.0.0.0/::) tidak berguna untuk URL yang dikirim ke
        # target; tampilkan loopback. Dideteksi via `ipaddress` supaya tidak
        # ada literal bind-address (bandit B104) di kode.
        try:
            wildcard = ipaddress.ip_address(self.host).is_unspecified
        except ValueError:
            wildcard = self.host == ""
        display = "127.0.0.1" if wildcard else self.host
        return f"http://{display}:{self.port}"

    def url_for(self, token: str) -> str:
        return f"{self.url_base}/c/{token}"

    def info(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "host": self.host,
            "port": self.port,
            "base_url": self.url_base if self.running else "",
        }
