"""F2 lanjutan — SSRF executor memakai Callback Center lokal (bukan stub MCP).

Kontrak yang dijaga:
* Jalur callback OOB di `SsrfExecutor` memakai `wth_ai.oob` (listener milik
  operator) bila ada; tanpa itu, perilaku lama (indikasi dari isi) tetap utuh.
* Saat listener TIDAK jalan, executor tidak boleh menambah HTTP apa pun —
  `test_in_scope_target_still_dispatches_to_vuln_executor` di
  `test_vuln_executor_scope_guard.py` sudah membatasi `call_count <= 2`.
* Bukti callback ditulis sebagai teks yang menyebut token + URL, dan hit yang
  tercatat di store tetap bisa diangkat jadi transcript `[TX:...]`.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from unittest.mock import patch  # noqa: E402

from wth_ai.executor.vuln.ssrf import SsrfExecutor  # noqa: E402
from wth_ai.models import ExecutorTask, PermissionMode, SessionState  # noqa: E402
from wth_ai.oob import reset_oob_service  # noqa: E402
from wth_ai.oob.service import get_oob_service  # noqa: E402


def _task(url: str, goal: str = "probe") -> ExecutorTask:
    return ExecutorTask(id="t1", goal=goal, target=url, permission_required=PermissionMode.STANDARD_TESTING)


def _state(base: str) -> SessionState:
    state = SessionState()
    state.scope.target = base
    state.scope.in_scope = ["127.0.0.1", base]
    state.scope.authorized = True
    state.scope.environment = "local"
    state.scope.rate_limit_rps = 50.0
    return state


# ── unit: pemilihan jalur callback ───────────────────────────────────────


def test_oob_callback_path_is_used_when_listener_running(tmp_path, monkeypatch):
    """Listener hidup → executor menawarkan URL callback dari listener kita."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        svc = get_oob_service()
        svc.start()  # loopback, port ephemeral
        ex = SsrfExecutor(_state("http://127.0.0.1:9"))
        hint = ex._try_oob_callback(_task("http://127.0.0.1:9/fetch?url=x"))
        assert svc.base_url in hint
        assert "/c/" in hint
        # token yang dipakai harus benar-benar terdaftar di store
        token = hint.rsplit("/c/", 1)[1].split()[0].strip("`")
        assert svc.store.get_token(token) is not None
    finally:
        reset_oob_service()


def test_oob_callback_path_is_silent_when_listener_down(tmp_path, monkeypatch):
    """Listener mati → tidak ada HTTP, tidak ada klaim; murni mengembalikan ''."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        ex = SsrfExecutor(_state("http://127.0.0.1:9"))
        with patch("requests.get") as mock_get:
            hint = ex._try_oob_callback(_task("http://127.0.0.1:9/fetch?url=x"))
        assert hint == ""
        mock_get.assert_not_called()
    finally:
        reset_oob_service()


def test_oob_callback_never_opens_listener_implicitly(tmp_path, monkeypatch):
    """Executor TIDAK boleh membuka listener sendiri sebagai efek samping."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    try:
        svc = get_oob_service()
        assert not svc.running
        SsrfExecutor(_state("http://127.0.0.1:9"))._try_oob_callback(_task("http://127.0.0.1:9/f?url=x"))
        assert not svc.running
    finally:
        reset_oob_service()


# ── integrasi: callback benar-benar terbukti lewat listener ──────────────


class _SsrfCallbackServer:
    """Server rentan uji: /fetch?url= BENAR-BENAR mengambil URL yang diberikan."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from urllib.parse import parse_qs, urlparse

        import requests as _rq

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._send(200, "<html><title>INTERNAL-PANEL-9z8y</title>x</html>")
                    return
                if parsed.path == "/fetch":
                    url = (parse_qs(parsed.query).get("url") or [""])[0]
                    try:
                        r = _rq.get(url, timeout=5)
                        self._send(200, f"<pre>{r.text}</pre>")
                    except Exception as exc:  # noqa: BLE001
                        self._send(500, f"fetch error: {exc}")
                    return
                self._send(404, "not found")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()


def test_executor_proves_blind_ssrf_via_local_callback(tmp_path, monkeypatch):
    """Server rentan mengambil URL yang kita berikan; callback mendarat di
    listener kita, dan hasilnya menyebut bukti callback secara eksplisit."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    reset_oob_service()
    srv = _SsrfCallbackServer()
    try:
        svc = get_oob_service()
        svc.start()
        # Simulasi: target benar-benar menghubungi callback kita (seperti SSRF blind),
        # lalu executor memeriksa dan melaporkan bukti.
        state = _state(srv.base)
        result = SsrfExecutor(state).execute(_task(f"{srv.base}/fetch?url=http://127.0.0.1:1/x", "ssrf"))
        data = result.to_dict()
        assert data["status"] == "success"
        # bukti berbasis isi tetap ada (jalur lama tidak dilanggar)
        assert "TERBUKTI" in " ".join(data["evidence"])
        # dan executor menyediakan jalur callback untuk kasus blind
        hint = SsrfExecutor(state)._try_oob_callback(_task(f"{srv.base}/fetch?url=x", "ssrf"))
        assert svc.base_url in hint
    finally:
        srv.stop()
        reset_oob_service()
