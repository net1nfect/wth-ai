"""Proxy operator (WTH_PROXY) dipakai fetch recon tanpa mengubah pemanggil."""

import sys
import types

from wth_ai.recon import http as recon_http


def test_proxy_map_none_without_env(monkeypatch):
    monkeypatch.delenv("WTH_PROXY", raising=False)
    assert recon_http.proxy_map() is None


def test_proxy_map_from_env(monkeypatch):
    monkeypatch.setenv("WTH_PROXY", "http://127.0.0.1:8080")
    assert recon_http.proxy_map() == {"http": "http://127.0.0.1:8080",
                                      "https": "http://127.0.0.1:8080"}


class _FakeResponse:
    def __init__(self):
        self.is_redirect = False
        self.status_code = 200
        self.headers = {"Content-Type": "text/html"}
        self.encoding = "utf-8"
        self.cookies = []
        self.url = "http://target.test/"
        self.raw = types.SimpleNamespace(read=lambda *a, **k: b"<html>ok</html>")

    def close(self):
        pass


def _install_fake_requests(monkeypatch):
    captured = {}

    def request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeResponse()

    module = types.SimpleNamespace(request=request)
    monkeypatch.setitem(sys.modules, "requests", module)
    return captured


def test_fetch_passes_proxies_to_requests(tmp_path, monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("WTH_PROXY", "http://127.0.0.1:8080")
    captured = _install_fake_requests(monkeypatch)
    monkeypatch.setattr(recon_http, "host_is_blocked", lambda *a, **k: None)

    response = recon_http.fetch("http://target.test/")
    assert response.status == 200
    assert captured["kwargs"]["proxies"] == {"http": "http://127.0.0.1:8080",
                                             "https": "http://127.0.0.1:8080"}


def test_fetch_without_proxy_passes_none(monkeypatch):
    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.delenv("WTH_PROXY", raising=False)
    captured = _install_fake_requests(monkeypatch)
    monkeypatch.setattr(recon_http, "host_is_blocked", lambda *a, **k: None)

    recon_http.fetch("http://target.test/")
    assert captured["kwargs"]["proxies"] is None


def test_egress_guard_still_wins_over_proxy(monkeypatch):
    from wth_ai.netguard import EgressGuard

    monkeypatch.delenv("WTH_TEST_MODE", raising=False)
    monkeypatch.setenv("WTH_PROXY", "http://127.0.0.1:8080")
    captured = _install_fake_requests(monkeypatch)
    monkeypatch.setattr(recon_http, "host_is_blocked", lambda *a, **k: None)
    recon_http.set_egress_guard(EgressGuard.from_patterns(["10.0.0.0/8"]))
    try:
        response = recon_http.fetch("http://target.test/")
    finally:
        recon_http.set_egress_guard(None)
    assert response.error
    assert "method" not in captured  # tidak ada request yang dikirim