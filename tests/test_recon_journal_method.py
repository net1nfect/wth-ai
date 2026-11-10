"""Jurnal proxy mencatat method yang benar-benar dipakai.

Item 9: `_record_exchange` menulis `"method": "GET"` harfiah, sehingga probe
HEAD tercatat sebagai GET di jurnal dan bukti yang diturunkan darinya salah.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import wth_ai.recon.http as rhttp

class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"get-body"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return

@pytest.fixture()
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    # Modul test lain memasang WTH_TEST_MODE=1 saat import dan tidak
    # mengembalikannya; `fetch` membaca env itu per panggilan dan mematikan
    # jaringan. Test ini bicara ke socket nyata, jadi mode itu harus mati.
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/probe"
    try:
        yield url
    finally:
        srv.shutdown()
        srv.server_close()

def _journal(tmp_path) -> list[dict]:
    from wth_ai.proxy.local import journal_path

    path = journal_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

def test_head_request_recorded_as_head(server, tmp_path):
    """HEAD tercatat HEAD di jurnal, bukan GET."""
    res = rhttp.fetch(server, method="HEAD", allow_private=True)
    assert res.status == 200

    rows = _journal(tmp_path)
    assert rows, "jurnal kosong"
    assert rows[-1]["request"]["method"] == "HEAD"

def test_get_request_recorded_as_get(server, tmp_path):
    """GET tetap tercatat GET."""
    rhttp.fetch(server, method="GET", allow_private=True)

    rows = _journal(tmp_path)
    assert rows[-1]["request"]["method"] == "GET"
