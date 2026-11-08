"""P1-5: SSRF redirect bypass di `research/engine.py`.

Temuan audit segar 2026-11-06: `_get()` (penelitian) memakai
`requests.get(..., allow_redirects=True)` — urutan redirect diikuti buta tanpa
memeriksa ulang host setiap hop. Recon HTTP (`recon/http.py`) sudah menutup
celah ini (WU-203: redirect diikuti MANUAL, host hop diperiksa ulang terhadap
guard), tapi jalur research tidak. URL publik yang 30x-redirect ke alamat
internal (mis. `http://127.0.0.1/metadata`) adalah bypass SSRF klasik.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")


class _FakeResp:
    """Objek respons minimal untuk `requests.get` yang di-monkeypatch."""

    def __init__(self, url, status, location=None):
        self.url = url
        self.status_code = status
        self.text = "body"
        self._location = location

    @property
    def is_redirect(self) -> bool:
        return self._location is not None

    @property
    def headers(self) -> dict:
        return {"Location": self._location} if self._location else {}

    def close(self) -> None:
        pass


def _engine(monkeypatch, responses: list[tuple[str, int, str | None]]) -> "Researcher":
    """Monkeypatch `requests.get` di modul engine agar jalur requests NYATA
    (manual redirect) yang dieksekusi, bukan stub http_get."""
    from wth_ai.research import engine as engine_mod

    call = {"i": 0}

    def fake_requests_get(url, *, timeout=None, headers=None, allow_redirects=True, **kwargs):
        assert allow_redirects is False, (
            "fix harus memakai allow_redirects=False untuk redirect manual"
        )
        i = call["i"]
        call["i"] = (i + 1) % len(responses)
        _, s, loc = responses[i]
        return _FakeResp(url, s, loc)

    # `requests` di-import lokal di dalam `_get` (`import requests`), yang mengikat
    # entri sys.modules asli — jadi patch modul `requests` nyata.
    import requests as requests_mod

    monkeypatch.setattr(requests_mod, "get", fake_requests_get)
    return engine_mod.Researcher()


def test_fetch_rejects_redirect_to_internal_address(monkeypatch):
    # URL publik redirect ke metadata cloud internal (127.0.0.1).
    engine = _engine(
        monkeypatch,
        [("http://public.example.com/docs", 302, "http://127.0.0.1/metadata")],
    )
    note = engine.fetch("https://public.example.com/docs")
    assert note.error, "redirect ke alamat internal harus diblokir, bukan diikuti"
    joined = (note.error + " " + note.snippet).lower()
    assert "diblokir" in joined or "ssrf" in joined or "private" in joined, note.error


def test_fetch_rejects_redirect_to_link_local(monkeypatch):
    engine = _engine(
        monkeypatch,
        [("http://public.example.com/x", 302, "http://169.254.169.254/latest/meta-data/")],
    )
    note = engine.fetch("https://public.example.com/x")
    assert note.error, "redirect ke link-local harus diblokir"
    joined = (note.error + " " + note.snippet).lower()
    assert "diblokir" in joined or "ssrf" in joined or "private" in joined, note.error


def test_get_follows_redirect_to_safe_external_host(monkeypatch):
    # Kontrol positif: redirect ke host publik lain TIDAK boleh diblokir.
    engine = _engine(
        monkeypatch,
        [
            ("http://public.example.com/start", 302, "http://public2.example.com/doc"),
            ("http://public2.example.com/doc", 200, None),
        ],
    )
    resp = engine._get("http://public.example.com/start")
    assert resp.status_code == 200, resp
    assert resp.url.endswith("/doc")


def test_initial_host_is_still_gated(monkeypatch):
    # Guard host awal tetap bekerja: target internal langsung ditolak di `fetch`.
    engine = _engine(monkeypatch, [("http://127.0.0.1/metadata", 200, None)])
    note = engine.fetch("http://127.0.0.1/metadata")
    assert note.error
    assert "ditolak" in note.error.lower()


def test_get_json_rejects_redirect_to_internal_address(monkeypatch):
    """F4 (review): `_get_json` (keyed API) punya celah SSRF-redirect yang sama —
    redirect ke alamat internal harus ditolak, bukan diikuti."""
    from wth_ai.research import engine as engine_mod

    call = {"i": 0}

    def fake_requests_get(url, *, timeout=None, headers=None, allow_redirects=True, **kwargs):
        assert allow_redirects is False
        if call["i"] == 0:
            call["i"] = 1
            return _FakeResp("http://api.example.com/search", 302, "http://127.0.0.1/private")
        return _FakeResp(url, 200, None)

    import requests as requests_mod

    monkeypatch.setattr(requests_mod, "get", fake_requests_get)
    engine = engine_mod.Researcher()
    with pytest.raises(engine_mod.SSRFRedirectBlocked):
        engine._get_json("http://api.example.com/search", headers={"X-Key": "k"}, timeout=5)
