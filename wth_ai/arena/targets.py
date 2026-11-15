"""Arena server — target lokal SENGAJA rentan untuk harness regresi kemampuan.

Prinsip arena: ya "kepintaran naik" itu klaim, dan klaim harus diuji. Server ini
stdlib-only, 127.0.0.1, port ephemeral, dan SANGAT deterministik — setiap endpoint
membocorkan bug sesuai kelasnya, dan TIDAK bocor kelas lain.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen

PASSWD_FIXTURE = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
)
UID_FIXTURE = "uid=1000(arena) gid=1000(arena) groups=1000(arena)\n"
SELFTEST_MARKER = "arena-selftest-marker-z9y8x7"


class ArenaHandler(BaseHTTPRequestHandler):
    """Router arena. Data hidup di self.server (ArenaServer)."""

    def log_message(self, *a):  # senyap
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)

        if p.path == "/":
            # Halaman root HARUS punya marker khas >6 char utk _distinctive_marker()
            self._send(200, b"<html><title>WTH-Arena</title><h1>WTH-Arena Internal Ops</h1>root page</html>")

        elif p.path == "/sqli":
            v = q.get("id", [""])[0]
            if "'" in v:
                self._send(500, b"you have an error in your sql syntax near '''")
            elif "AND 1=2" in v:
                self._send(200, b"MISMATCHED-BOOLEAN-RESPONSE")
            elif "SLEEP" in v:
                self._send(200, b"ok")  # tanpa delay -> time-blind TIDAK false-positive
            else:
                self._send(200, b"query ok baseline-" + b"a" * 220)

        elif p.path == "/ssti":
            v = q.get("name", [""])[0]
            rendered = v.replace("{{7*7}}", "49").replace("{{7*'7'}}", "7777777")
            self._send(200, f"hello {rendered}".encode())

        elif p.path == "/cmdi":
            v = q.get("host", [""])[0]
            if "id" in v:
                self._send(200, UID_FIXTURE.encode())  # output `id` nyata terpantul
            else:
                self._send(200, b"lookup ok")

        elif p.path == "/trav":
            v = unquote(q.get("file", [""])[0])
            norm = os.path.normpath(v)
            if norm.endswith("/etc/passwd"):
                self._send(200, PASSWD_FIXTURE.encode())
            else:
                self._send(404, b"not found")

        elif p.path == "/xss":
            v = q.get("q", [""])[0]
            self._send(200, f'<div>hasil: {v} /1234abcdef</div>'.encode())  # tidak di-escape

        elif p.path == "/ssrf":
            v = q.get("url", [""])[0]
            if isinstance(v, str) and v.startswith("http://127.0.0.1"):
                try:
                    picked = urlopen(v, timeout=5).read(5000).decode("utf-8", "replace")
                    self._send(200, f"remote:{picked}".encode())
                except Exception:  # noqa: BLE001
                    self._send(502, b"fetch-err")
            else:
                self._send(400, b"missing url param")

        elif p.path.startswith("/api/user/"):
            uid = p.path.rsplit("/", 1)[1]
            users = {
                "1": {"id": 1, "email": "admin@arena.local", "role": "admin"},
                "2": {"id": 2, "email": "user2@arena.local", "role": "user"},
                "3": {"id": 3, "email": "user3@arena.local", "role": "user"},
            }
            if uid in users:
                self._send(200, json.dumps(users[uid]).encode(), "application/json")
            else:
                self._send(404, b"{}")

        elif p.path == "/api/me":
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                self._send(200, b'{"sub": 1, "role": "user"}', "application/json")
            else:
                self._send(401, b'unauthorized')

        elif p.path == "/admin/export":
            # Chain scenario: aktor diambil dari parameter (bukan session server).
            # Email admin bisa didapat dari IDOR /api/user/1 lebih dulu; endpoint
            # ini hanya melayani aktor admin. Terdaftar sebagai rantai, bukan
            # kasus terisolasi di ARENA_CASES.
            actor = q.get("actor", [""])[0]
            if actor == "admin@arena.local":
                self._send(200, b'{"export":"ARENA-ADMIN-EXPORT-7x6w5v","users":3}')
            else:
                self._send(403, b"forbidden")

        elif p.path == "/arena-selftest":
            self._send(200, f"health page {SELFTEST_MARKER}".encode())

        else:
            self._send(404, b"nf")

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/redeem":
            # GAP-arena race: burst 20 thread — 5 pertama sukses (body unik),
            # sisanya 409. Counter persisten di server.
            arena: "ArenaServer" = self.server  # type: ignore[assignment]
            with arena.redeem_lock:
                arena.redeem_counter += 1
                n = arena.redeem_counter
            if n <= 5:
                self._send(200, f"redeemed-token-{n}-{os.urandom(4).hex()}".encode(),
                           "application/json")
            else:
                self._send(409, b"already-redeemed")
        else:
            self._send(404, b"nf")
        return


class ArenaServer:
    """Satu server untuk semua kasus arena. Start/stop per run."""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ArenaHandler)
        self.port = self.httpd.server_address[1]
        # Counter race harus hidup DI SERVER (handler baca self.server), bukan
        # di wrapper — ketaah dari smoke arena pertama: AttributeError redeem_lock.
        self.httpd.redeem_counter = 0  # type: ignore[attr-defined]
        self.httpd.redeem_lock = threading.Lock()  # type: ignore[attr-defined]
        self.redeem_counter = 0
        self.redeem_lock = threading.Lock()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass
