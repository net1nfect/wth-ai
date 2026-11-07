"""Proxy terstruktur (Task 10 port-strix): jurnal lokal + adapter Caido.

Adapter lokal membaca jurnal request/response yang ditulis `recon/http.py`,
sehingga agen bisa mengintip riwayat tanpa proxy eksternal. Replay tetap
melewati `EgressGuard` — proxy tidak boleh memperluas allowlist.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from wth_ai.netguard import EgressDenied, EgressGuard
from wth_ai.proxy import ProxyUnavailable
from wth_ai.proxy.caido import CaidoAdapter
from wth_ai.proxy.local import LocalProxyAdapter, append_exchange
from wth_ai.recon import http as recon_http


def test_history_baru_dulu_dan_memuat_request_response(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    append_exchange(
        tx_id="TX-1",
        request={"method": "GET", "url": "https://a.example/x", "headers": {}},
        response={"status": 200, "body": "hi"},
    )
    append_exchange(
        tx_id="TX-2",
        request={"method": "GET", "url": "https://a.example/y", "headers": {}},
        response={"status": 404, "body": "nope"},
    )
    entries = LocalProxyAdapter().history(limit=20)
    assert [e["tx_id"] for e in entries] == ["TX-2", "TX-1"]
    assert entries[0]["request"]["url"] == "https://a.example/y"
    assert entries[0]["response"]["status"] == 404


def test_history_menghormati_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    for i in range(5):
        append_exchange(
            tx_id=f"TX-{i}",
            request={"method": "GET", "url": f"https://a.example/{i}"},
            response={"status": 200},
        )
    entries = LocalProxyAdapter().history(limit=2)
    assert [e["tx_id"] for e in entries] == ["TX-4", "TX-3"]


def test_jurnal_meredaksi_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    append_exchange(
        tx_id="TX-S",
        request={
            "method": "GET",
            "url": "https://a.example/",
            "headers": {"Authorization": "Bearer rahasia-panjang-sekali"},
        },
        response={"status": 200, "body": "ok"},
    )
    path = tmp_path / "data" / "proxy" / "history.jsonl"
    assert "rahasia-panjang-sekali" not in path.read_text()


def test_jurnal_rotasi_saat_melewati_batas(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.proxy import local as proxy_local

    monkeypatch.setattr(proxy_local, "MAX_JOURNAL_BYTES", 1)
    append_exchange(tx_id="TX-A", request={"method": "GET", "url": "https://a.example/1"}, response={"status": 200})
    append_exchange(tx_id="TX-B", request={"method": "GET", "url": "https://a.example/2"}, response={"status": 200})
    rotated = proxy_local.journal_path().with_name(proxy_local.ROTATED_NAME)
    assert rotated.exists()
    assert [e["tx_id"] for e in LocalProxyAdapter().history(limit=5)] == ["TX-B"]

def test_replay_memakai_egress_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    append_exchange(
        tx_id="TX-OUT",
        request={"method": "GET", "url": "https://evil.example/admin"},
        response={"status": 200},
    )
    monkeypatch.setattr(recon_http, "_EGRESS_GUARD", EgressGuard.from_patterns(["allowed.example"]))
    try:
        with pytest.raises(EgressDenied):
            LocalProxyAdapter().replay("TX-OUT")
    finally:
        monkeypatch.setattr(recon_http, "_EGRESS_GUARD", None)


def test_replay_entri_tidak_ada(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    with pytest.raises(KeyError):
        LocalProxyAdapter().replay("TX-hantu")


def test_caido_tanpa_env_adalah_proxy_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("WTH_CAIDO_URL", raising=False)
    with pytest.raises(ProxyUnavailable):
        CaidoAdapter().history()


def test_caido_host_di_luar_allowlist_ditolak(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_CAIDO_URL", "http://caido.local:8443")
    monkeypatch.setattr(recon_http, "_EGRESS_GUARD", EgressGuard.from_patterns(["allowed.example"]))
    try:
        with pytest.raises(EgressDenied):
            CaidoAdapter().history()
    finally:
        monkeypatch.setattr(recon_http, "_EGRESS_GUARD", None)


class _CaidoHandler(BaseHTTPRequestHandler):
    # Nama method ditentukan http.server, bukan gaya repo.
    def do_POST(self):
        body = json.dumps({
            "data": {
                "requests": {
                    "edges": [
                        {
                            "node": {
                                "id": "c-1",
                                "method": "GET",
                                "url": "https://a.example/x",
                                "status": 200,
                                "response": {"statusCode": 200, "body": "hi"},
                            }
                        }
                    ]
                }
            }
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # Senyapkan log test supaya output tetap bersih.
    def log_message(self, *args):
        return


def test_caido_history_dari_server_palsu(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _CaidoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("WTH_CAIDO_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setattr(recon_http, "_EGRESS_GUARD", None)
        entries = CaidoAdapter().history(limit=5)
        assert entries[0]["tx_id"] == "c-1"
        assert entries[0]["request"]["url"] == "https://a.example/x"
        assert entries[0]["response"]["status"] == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_fetch_mencatat_jurnal_saat_get_berhasil(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))

    class FakeResp:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"Content-Type": "text/plain"}
        raw = None
        cookies: ClassVar[list] = []
        url = "https://a.example/"
        encoding = "utf-8"

        def close(self):
            return None

        @property
        def is_redirect(self):
            return False

    class FakeRequests:
        @staticmethod
        def request(*args, **kwargs):
            return FakeResp()

    monkeypatch.setitem(__import__("sys").modules, "requests", FakeRequests)
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    resp = recon_http.fetch("https://a.example/")
    assert resp.status == 200
    entries = LocalProxyAdapter().history(limit=5)
    assert entries and entries[0]["request"]["url"] == "https://a.example/"
