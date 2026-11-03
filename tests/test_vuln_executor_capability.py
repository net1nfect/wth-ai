"""Kemampuan executor: apakah benar-benar menemukan bug, bukan hanya mencetak klaim?

Diuji terhadap server lokal yang SENGAJA rentan (stdlib, hanya 127.0.0.1), karena
pertanyaan operaturnya bukan "apakah kode ini jalan" melainkan "bisa exploit beneran
ga". Setiap test menegaskan BUKTI yang harus muncul, bukan sekadar status.

Empat perbaikan kemampuan yang diuji di sini (semuanya MELONGGARKAN pengujian, bukan
mengetatkannya):
* IDOR tidak lagi `blocked` tanpa akun uji -- kasus paling umum adalah endpoint
  terbuka yang membocorkan objek milik orang lain, dan itu bisa diuji tanpa akun;
* SSRF membuktikan dari ISI (penanda halaman internal muncul di respons), bukan dari
  error koneksi;
* AUTH menguji login (enumerasi user, lockout, kredensial default) alih-alih berhenti
  ketika tidak ada JWT;
* RACE mencoba method yang benar (POST kalau GET ditolak), karena endpoint POST yang
  diuji dengan GET tidak menguji apa pun.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.models import ExecutorTask, PermissionMode, SessionState
from wth_ai.executor.vuln.auth import AuthVulnExecutor
from wth_ai.executor.vuln.idor import IdorExecutor
from wth_ai.executor.vuln.race import RaceExecutor
from wth_ai.executor.vuln.ssrf import SsrfExecutor


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


class _Server:
    """Server uji: perilaku ditentukan oleh subclass lewat `handle`."""

    def __init__(self):
        self.requests: list[tuple[str, str, str]] = []
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()

    def _make_handler(self):
        outer = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: str, ctype: str = "text/html"):
                raw = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.requests.append(("GET", self.path, ""))
                outer.handle_get(self, self.path)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length).decode() if length else ""
                outer.requests.append(("POST", self.path, body))
                outer.handle_post(self, self.path, body)

        return _H

    def handle_get(self, h, path: str) -> None:  # pragma: no cover - di-override
        h._send(404, "not found")

    def handle_post(self, h, path: str, body: str) -> None:  # pragma: no cover
        h._send(404, "not found")


# --------------------------------------------------------------------------- #
# IDOR
# --------------------------------------------------------------------------- #
class _IdorServer(_Server):
    def handle_get(self, h, path: str) -> None:
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(path).query)
        uid = (qs.get("id") or ["1"])[0]
        users = {
            "1": {"id": 1, "name": "alice", "email": "alice@local", "role": "user"},
            "2": {"id": 2, "name": "bob", "email": "bob@local", "role": "admin"},
        }
        user = users.get(uid)
        if not user:
            h._send(404, json.dumps({"error": "not found"}), "application/json")
            return
        h._send(200, json.dumps(user), "application/json")


def test_idor_finds_a_real_object_leak_without_any_accounts():
    """Sebelumnya `blocked`; IDOR tanpa akun uji adalah kasus nyata."""
    srv = _IdorServer()
    try:
        state = _state(srv.base)
        result = IdorExecutor(state).execute(_task(f"{srv.base}/api/user?id=1", "idor"))
        data = result.to_dict()
        assert data["status"] == "success"
        assert data["risk"] == "high", data
        joined = " ".join(data["evidence"]).lower()
        assert "tanpa otorisasi" in joined
        assert "field objek" in joined
        pairs = state.metadata.get("request_pairs", [])
        assert len(pairs) >= 2, "bukti dua objek harus terekam"
        assert "alice" in pairs[0]["body_snippet"] and "bob" in pairs[1]["body_snippet"]
    finally:
        srv.stop()


class _Idor403Server(_Server):
    def handle_get(self, h, path: str) -> None:
        h._send(403, "forbidden")


def test_idor_reports_enforced_access_control():
    srv = _Idor403Server()
    try:
        result = IdorExecutor(_state(srv.base)).execute(_task(f"{srv.base}/api/user?id=1", "idor"))
        data = result.to_dict()
        assert data["status"] == "success"
        assert data["risk"] == "low"
        assert any("ditolak" in e.lower() or "ditegakkan" in e.lower() for e in data["evidence"]), data
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# SSRF
# --------------------------------------------------------------------------- #
class _SsrfServer(_Server):
    def handle_get(self, h, path: str) -> None:
        import requests as _rq
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(path)
        if parsed.path == "/":
            h._send(200, "<html><title>INTERNAL-PANEL-9z8y</title><body>x</body></html>")
            return
        if parsed.path == "/fetch":
            url = (parse_qs(parsed.query).get("url") or [""])[0]
            try:
                r = _rq.get(url, timeout=5)
                h._send(200, f"<pre>{r.text}</pre>")
            except Exception as exc:  # noqa: BLE001
                h._send(500, f"fetch error: {exc}")
            return
        h._send(404, "not found")


def test_ssrf_is_proven_from_fetched_content_not_from_an_error():
    srv = _SsrfServer()
    try:
        state = _state(srv.base)
        result = SsrfExecutor(state).execute(_task(f"{srv.base}/fetch?url=http://127.0.0.1:1/x", "ssrf"))
        data = result.to_dict()
        assert data["risk"] == "high", data
        joined = " ".join(data["evidence"])
        assert "TERBUKTI" in joined, data
        assert "INTERNAL-PANEL-9z8y" in joined, "penanda isi internal harus disebut sebagai bukti"
        assert len(state.metadata.get("request_pairs", [])) >= 2
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# AUTH
# --------------------------------------------------------------------------- #
class _EnumServer(_Server):
    def handle_get(self, h, path: str) -> None:
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(path).query)
        user = (qs.get("user") or [""])[0]
        if user in ("admin", "alice"):
            h._send(401, "wrong password for existing user")
        else:
            h._send(401, "unknown user")


def test_auth_detects_user_enumeration():
    srv = _EnumServer()
    try:
        result = AuthVulnExecutor(_state(srv.base)).execute(
            _task(f"{srv.base}/login?user=admin&pw=x", "auth"))
        data = result.to_dict()
        assert data["status"] == "success"
        assert data["risk"] == "medium", data
        assert any("enumerasi" in e.lower() for e in data["evidence"]), data
    finally:
        srv.stop()


class _DefaultCredsServer(_Server):
    def handle_get(self, h, path: str) -> None:
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(path).query)
        user = (qs.get("user") or [""])[0]
        pw = (qs.get("pw") or [""])[0]
        if user == "admin" and pw == "admin":
            h._send(200, "welcome admin")
        else:
            h._send(401, "invalid credentials")


def test_auth_finds_default_credentials():
    srv = _DefaultCredsServer()
    try:
        result = AuthVulnExecutor(_state(srv.base)).execute(
            _task(f"{srv.base}/login?user=admin&pw=x", "auth"))
        data = result.to_dict()
        assert data["risk"] == "high", data
        assert any("KREDENSIAL DEFAULT DITERIMA" in e for e in data["evidence"]), data
    finally:
        srv.stop()


class _LockoutServer(_Server):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    def handle_get(self, h, path: str) -> None:
        self.attempts += 1
        if self.attempts > 2:
            h._send(429, "too many attempts, account locked")
        else:
            h._send(401, "invalid credentials")


def test_auth_notices_a_lockout():
    """Sinyal harus dua arah: kalau lockout ADA, itu juga dilaporkan."""
    srv = _LockoutServer()
    try:
        result = AuthVulnExecutor(_state(srv.base)).execute(
            _task(f"{srv.base}/login?user=admin&pw=x", "auth"))
        data = result.to_dict()
        joined = " ".join(data["observations"])
        assert "lockout setelah" in joined and "True" in joined, data
        assert not any("Tidak ada lockout" in e for e in data["evidence"]), data
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# RACE
# --------------------------------------------------------------------------- #
class _RaceServer(_Server):
    """POST-only, dan benar-benar bisa direbut: GET ditolak 404."""

    def __init__(self):
        super().__init__()
        self.ok = 0
        self.lock = threading.Lock()

    def handle_post(self, h, path: str, body: str) -> None:
        if path != "/redeem":
            h._send(404, "not found")
            return
        with self.lock:
            allowed = self.ok < 20
            self.ok += 1
        h._send(200 if allowed else 409, "redeemed" if allowed else "already redeemed")


def test_race_switches_to_post_and_detects_the_window():
    """Endpoint POST diuji dengan GET = tidak menguji apa pun (terukur: 20x404)."""
    srv = _RaceServer()
    try:
        result = RaceExecutor(_state(srv.base)).execute(_task(f"{srv.base}/redeem", "race"))
        data = result.to_dict()
        assert any("method=POST" in o for o in data["observations"]), data
        assert data["risk"] == "high", data
        assert any("paralel mengembalikan 2xx" in e for e in data["evidence"]), data
        methods = {m for m, _p, _b in srv.requests}
        assert "GET" in methods and "POST" in methods, "harus mencoba GET lalu POST"
    finally:
        srv.stop()


class _SingleSuccessServer(_RaceServer):
    def handle_post(self, h, path: str, body: str) -> None:
        if path != "/redeem":
            h._send(404, "not found")
            return
        with self.lock:
            first = self.ok == 0
            self.ok += 1
        h._send(200 if first else 409, "redeemed" if first else "already redeemed")


def test_race_reports_no_finding_when_only_one_request_wins():
    """Kontrol negatif: tanpa jendela balapan, jangan mengarang temuan."""
    srv = _SingleSuccessServer()
    try:
        result = RaceExecutor(_state(srv.base)).execute(_task(f"{srv.base}/redeem", "race"))
        data = result.to_dict()
        assert data["risk"] == "low", data
        assert not data["evidence"], data
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# bukti hukum: tanpa bukti, tanpa klaim
# --------------------------------------------------------------------------- #
class _SafeServer(_Server):
    def handle_get(self, h, path: str) -> None:
        h._send(200, "<html><title>aman</title>same body for everyone</html>")


def test_no_evidence_means_low_risk_not_a_finding():
    """Semua executor harus tetap rendah risiko saat tidak ada bukti."""
    srv = _SafeServer()
    try:
        state = _state(srv.base)
        results = [
            IdorExecutor(state).execute(_task(f"{srv.base}/api/user?id=1", "idor")),
            SsrfExecutor(state).execute(_task(f"{srv.base}/fetch?url=x", "ssrf")),
            AuthVulnExecutor(state).execute(_task(f"{srv.base}/lain", "auth")),
        ]
        for result in results:
            data = result.to_dict()
            if data["risk"] == "high":
                assert data["evidence"], f"risiko tinggi tanpa bukti: {data}"
        idor = results[0].to_dict()
        assert idor["risk"] == "low", "objek yang isinya sama bukan IDOR"
    finally:
        srv.stop()
