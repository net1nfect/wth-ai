"""Regresi GAP-002/003/004/005: http_probe cookie jar, redirect hop, multipart.

Diuji terhadap server lokal (stdlib) — perilaku TIDAK dilimit (WTH_NO_LIMITS=0
di env test via conftest), scope eksplisit di-set untuk kasus out-of-scope.
"""

from __future__ import annotations

import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest  # noqa: E402

from wth_ai.config import WTHConfig  # noqa: E402
from wth_ai.permission_manager import PermissionManager  # noqa: E402
from wth_ai.scope_manager import ScopeManager  # noqa: E402
from wth_ai.terminal.bridge import AgentTerminalBridge  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/login":
            self.send_response(302)
            self.send_header("Location", "/portal")
            self.send_header("Set-Cookie", "session=abc123; Path=/")
            self.end_headers()
        elif self.path == "/portal":
            self.send_response(200); self.end_headers()
            self.wfile.write(f"PORTAL {self.headers.get('Cookie', '(none)')}".encode())
        elif self.path == "/redir-rel":
            self.send_response(302); self.send_header("Location", "portal"); self.end_headers()
        elif self.path == "/redir-out":
            self.send_response(302); self.send_header("Location", "http://evil.tld/x"); self.end_headers()
        elif self.path == "/raw":
            self.send_response(200); self.end_headers(); self.wfile.write(b"raw-body-ok")
        else:
            self.send_response(404); self.end_headers(); self.wfile.write(b"nf")

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(ln if ln else 0)
        multipart = "boundary=" in (self.headers.get("Content-Type") or "")
        marker = b"TEST-UPLOAD-MARKER" in data
        self.send_response(200); self.end_headers()
        self.wfile.write(f"received multipart={multipart} marker={marker}".encode())


@pytest.fixture(scope="module")
def server(base_url_fix=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _bridge(server_http_url: str) -> AgentTerminalBridge:
    config = WTHConfig.load()
    scope = ScopeManager()
    scope.set_scope(target="example.com", in_scope=["localhost", "127.0.0.1"],
                    authorized=True)
    return AgentTerminalBridge(config, PermissionManager(), scope, "test-probe")


def test_redirect_chain_captured_and_final_response(server):
    bridge = _bridge(server)
    r = bridge.http_probe("GET", f"{server}/redir-rel")
    # GAP-004: hop tercatat, response final yang diberikan.
    assert any("302" in hop for hop in r.redirect_chain)
    assert r.status_code == 200
    assert "PORTAL" in r.stdout
    # url hasil = hop terakhir (bukan asal).
    assert r.url.endswith("/portal")


def test_redirect_out_of_scope_stop_mid_chain(server):
    bridge = _bridge(server)
    r = bridge.http_probe("GET", f"{server}/redir-out")
    assert any("302" in hop for hop in r.redirect_chain)
    assert any("STOP" in hop and "out-of-scope" in hop.lower() for hop in r.redirect_chain)
    # Respons terakhir = redirect sendiri (belum diikuti).
    assert r.status_code == 302


def test_follow_redirects_false_returns_first_hop(server):
    bridge = _bridge(server)
    r = bridge.http_probe("GET", f"{server}/redir-rel", follow_redirects=False)
    assert r.status_code == 302
    assert any("302" in hop for hop in r.redirect_chain)
    assert not r.stdout.startswith("PORTAL")


def test_cookie_jar_persists_between_probes(server):
    bridge = _bridge(server)
    # Login via redirect & Set-Cookie -> jar menyimpan.
    bridge.http_probe("GET", f"{server}/login")
    assert "session" in bridge._http().cookies
    # Probe berikutnya membawa cookie otomatis.
    r = bridge.http_probe("GET", f"{server}/portal")
    assert "session=abc123" in r.stdout
    # Session-clear memutus jar.
    bridge.http_probe("GET", f"{server}/portal", session_clear=True)
    assert "session" not in bridge._http().cookies


def test_multipart_upload_is_sent_with_boundary(server):
    bridge = _bridge(server)
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="wb") as f:
        f.write(b"print('x')  # TEST-UPLOAD-MARKER\n")
        path = f.name
    r = bridge.http_probe("POST", f"{server}/upload", files={"exploit": path}, approved=True)
    os.unlink(path)
    assert not r.blocked
    assert r.status_code == 200
    # Buktikan multipart terkirim: boundary ada & isi file tervapor.
    assert "multipart=True" in r.stdout
    assert "marker=True" in r.stdout


def test_unknown_upload_file_is_blocked(server):
    bridge = _bridge(server)
    r = bridge.http_probe("POST", f"{server}/upload",
                          files={"exploit": "/tmp/tidak-ada-file-ini"}, approved=True)
    assert r.blocked
    assert "gagal dibuka" in r.block_reason


def test_probe_keeps_analysis_and_headers(server):
    bridge = _bridge(server)
    r = bridge.http_probe("GET", f"{server}/raw")
    assert r.status_code == 200
    assert "raw-body-ok" in r.stdout
    assert isinstance(r.response_headers, dict)
    assert r.analysis  # analyze_response mengisi


def test_missing_probe_target_blocked_fast(server):
    # (opsional) port mati -> RequestException ditangkap JADI hasil, bukan raise.
    bridge = _bridge(server)
    dead = server.rsplit(":", 1)[0] + ":1"
    r = bridge.http_probe("GET", f"{dead}/x")
    assert r.exit_code == 1
    assert r.stderr
