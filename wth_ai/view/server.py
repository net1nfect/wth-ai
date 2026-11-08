"""Dashboard bundel bukti, read-only (izin berbatas §1.2 poin 1).

Empat batas yang dijaga modul ini, semuanya disengaja:

* bind hanya ``127.0.0.1`` — tidak ada opsi host;
* tidak ada rute mutasi — semua selain ``GET``/``HEAD`` dijawab 405;
* token acak per proses, wajib di setiap request (403 bila salah/hilang);
* server hanya dibuat lewat pemanggilan eksplisit oleh operator, dan awal
  hidupnya dicatat di ``audit.log``.

Server ini membaca bundel yang sudah ditulis. Ia tidak memverifikasi isi bundel
— itu tugas ``wth-ai verify`` — supaya permukaan HTTP tidak punya jalur kedua
untuk memutuskan keaslian bukti.
"""

from __future__ import annotations

import hmac
import html
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from wth_ai.audit import audit_log

HOST = "127.0.0.1"
#: Bundel besar (banyak temuan/artefak) tetap harus muat di memori; batas ini
#: mencegah satu file rusak menghabiskan RAM.
MAX_BYTES = 8 * 1024 * 1024

class ViewServer:
    """Server HTTP read-only untuk satu direktori bundel."""

    def __init__(
        self,
        bundle_dir: Path,
        *,
        port: int = 0,
        token: str | None = None,
        host: str = HOST,
    ) -> None:
        if host != HOST:
            # Bukan parameter yang bisa dilonggarkan: dashboard lokal tidak
            # boleh mendengarkan antarmuka jaringan.
            raise ValueError(f"view hanya boleh bind {HOST}, bukan {host}")
        self.bundle_dir = Path(bundle_dir)
        self.token = token or secrets.token_urlsafe(24)
        self._httpd = ThreadingHTTPServer((HOST, port), _handler_for(self))
        self._thread: threading.Thread | None = None

    # -- siklus hidup --------------------------------------------------

    def start(self) -> str:
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="wth-view", daemon=True
        )
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        audit_log("view_start", host=host, port=port, bundle=str(self.bundle_dir))
        return self.url

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    @property
    def server_address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    @property
    def url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    # -- pembacaan bundel ----------------------------------------------

    def _read_json(self, name: str) -> Any:
        path = self.bundle_dir / name
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        if len(raw.encode("utf-8", errors="replace")) > MAX_BYTES:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def findings(self) -> list[dict[str, Any]]:
        data = self._read_json("findings.json")
        return data if isinstance(data, list) else []

    def manifest(self) -> dict[str, Any]:
        data = self._read_json("manifest.json")
        return data if isinstance(data, dict) else {}

    def index_html(self) -> str:
        findings = self.findings()
        manifest = self.manifest()
        rows = "".join(
            "<tr><td>{id}</td><td>{state}</td><td>{severity}</td><td>{hyp}</td></tr>".format(
                id=html.escape(str(f.get("id", ""))),
                state=html.escape(str(f.get("state", ""))),
                severity=html.escape(str(f.get("severity", ""))),
                hyp=html.escape(str(f.get("hypothesis", ""))),
            )
            for f in findings
        )
        return (
            "<!doctype html><meta charset='utf-8'>"
            "<title>WTH-AI bundle (read-only)</title>"
            f"<h1>Bundel bukti</h1><p>root_hash: <code>{html.escape(str(manifest.get('root_hash', '-')))}</code></p>"
            f"<p>{len(findings)} temuan · <a href='/findings?token=TOKEN'>JSON</a> · "
            f"<a href='/manifest?token=TOKEN'>manifest</a></p>"
            "<table><thead><tr><th>id</th><th>state</th><th>severity</th>"
            f"<th>hypothesis</th></tr></thead><tbody>{rows}</tbody></table>"
        )

def _handler_for(server: ViewServer) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _authorized(self) -> bool:
            header = self.headers.get("X-WTH-Token", "")
            query = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            given = header or query
            return bool(given) and hmac.compare_digest(given, server.token)

        def _send(self, status: int, body: str, content_type: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - nama wajib dari BaseHTTPRequestHandler
            self._route()

        def do_HEAD(self) -> None:  # noqa: N802
            self._route()

        def _reject_mutation(self) -> None:
            self._send(405, "hanya GET", "text/plain; charset=utf-8")

        do_POST = do_PUT = do_DELETE = do_PATCH = _reject_mutation  # noqa: N815

        def _route(self) -> None:
            if not self._authorized():
                self._send(403, "token salah atau hilang", "text/plain; charset=utf-8")
                return
            path = urlparse(self.path).path
            if path == "/":
                self._send(200, server.index_html(), "text/html; charset=utf-8")
            elif path == "/findings":
                self._send(200, json.dumps(server.findings(), indent=2),
                           "application/json; charset=utf-8")
            elif path == "/manifest":
                self._send(200, json.dumps(server.manifest(), indent=2),
                           "application/json; charset=utf-8")
            else:
                self._send(404, "tidak ditemukan", "text/plain; charset=utf-8")

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # Token tidak boleh masuk log akses; audit sudah mencatat view_start.
            return

    return _Handler

def serve(bundle_dir: Path, *, port: int = 0, token: str | None = None) -> ViewServer:
    """Buat + jalankan server. Pemanggil bertanggung jawab memanggil `stop()`."""
    server = ViewServer(bundle_dir, port=port, token=token)
    server.start()
    return server
