"""Setiap hop redirect harus lewat EgressGuard, bukan hanya URL awal.

Item 3: `_EGRESS_GUARD.check` hanya dipanggil untuk URL awal. URL in-scope yang
30x ke host luar allowlist diikuti, dan `audit.log` tidak mencatat
`EGRESS_DENIED` -- melanggar invarian AGENTS.md §3(5).

Dua server memakai alias loopback berbeda (127.0.0.2 / 127.0.0.3) karena
EgressGuard mencocokkan host, bukan port: dengan satu host keduanya tidak bisa
dibedakan allowlist-nya.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import wth_ai.recon.http as rhttp

ALLOWED_HOST = "127.0.0.2"
DENIED_HOST = "127.0.0.3"
#: EgressGuard.from_patterns memperlakukan string tanpa "/" sebagai NAMA host,
#: dan `allows()` membandingkan IP literal hanya terhadap CIDR. Karena itu
#: alamat literal di allowlist ditulis sebagai /32 (di produksi pola datang dari
#: CIDR scope card, jadi bentuk ini yang benar-benar dipakai).
ALLOW_PATTERN = "127.0.0.2/32"
DENY_PATTERN = "127.0.0.3/32"

class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"reached"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return

class _Redirect(BaseHTTPRequestHandler):
    target = ""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", self.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return

def _serve(host: str, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

@pytest.fixture()
def servers():
    """Redirect di ALLOWED_HOST menunjuk ke DENIED_HOST."""
    deny_srv = _serve(DENIED_HOST, _Ok)
    redir_srv = _serve(ALLOWED_HOST, _Redirect)
    _Redirect.target = f"http://{DENIED_HOST}:{deny_srv.server_address[1]}/"
    try:
        yield {
            "entry": f"http://{ALLOWED_HOST}:{redir_srv.server_address[1]}/",
            "allowed": ALLOWED_HOST,
            "denied": DENIED_HOST,
        }
    finally:
        for srv in (deny_srv, redir_srv):
            srv.shutdown()
            srv.server_close()

def _fetch(servers, monkeypatch, tmp_path, allowed_patterns):
    from wth_ai.netguard import EgressGuard

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    # Modul test lain memasang WTH_TEST_MODE=1 saat import dan tidak
    # mengembalikannya; `fetch` membaca env itu per panggilan dan mematikan
    # jaringan. Test ini bicara ke socket nyata, jadi mode itu harus mati.
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    rhttp.set_egress_guard(EgressGuard.from_patterns(allowed_patterns))
    try:
        # allow_private: guard SSRF diloloskan supaya yang diuji murni
        # keputusan EgressGuard. Pada instalasi nyata allow_private tetap False.
        return rhttp.fetch(servers["entry"], allow_private=True)
    finally:
        rhttp.set_egress_guard(None)

def test_redirect_hop_outside_allowlist_is_blocked(servers, monkeypatch, tmp_path):
    """Hop kedua ke host di luar allowlist ditolak, bukan diikuti."""
    res = _fetch(servers, monkeypatch, tmp_path, [ALLOW_PATTERN])

    assert res.error, "redirect ke host di luar allowlist seharusnya gagal"
    assert "redirect blocked" in res.error
    assert res.text == "", "body host kedua tidak boleh terbaca"

def test_redirect_block_writes_egress_denied_audit(servers, monkeypatch, tmp_path):
    """Penolakan menulis EGRESS_DENIED ke audit.log, bukan diam."""
    _fetch(servers, monkeypatch, tmp_path, [ALLOW_PATTERN])

    log = Path(tmp_path) / "logs" / "audit.log"
    assert log.is_file(), "audit.log harus dibuat saat egress ditolak"
    assert "EGRESS_DENIED" in log.read_text(encoding="utf-8")

def test_redirect_inside_allowlist_is_followed(servers, monkeypatch, tmp_path):
    """Hop kedua yang diizinkan tetap diikuti -- perbaikan tidak mematahkan redirect."""
    res = _fetch(
        servers, monkeypatch, tmp_path,
        [ALLOW_PATTERN, DENY_PATTERN],
    )

    assert res.error is None, res.error
    assert res.text == "reached"
