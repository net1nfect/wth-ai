"""Kemampuan executor NEW (GAP-009/001): SQLi, SSTI, CMDi, traversal, nuclei.

Sama pola dengan test_vuln_executor_capability.py: server lokal stdlib yang
SENGAJA rentan, lalu menegaskan BUKTI dari evidence — bukan sekadar status code.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest  # noqa: E402

from wth_ai.models import ExecutorTask, PermissionMode, SessionState  # noqa: E402
from wth_ai.executor.vuln.sqli import SqliExecutor  # noqa: E402
from wth_ai.executor.vuln.ssti import SstiExecutor  # noqa: E402
from wth_ai.executor.vuln.cmdi import CmdiExecutor  # noqa: E402
from wth_ai.executor.vuln.traversal import PathTraversalExecutor  # noqa: E402
from wth_ai.executor.vuln.nuclei import NucleiExecutor  # noqa: E402


def _task(url: str) -> ExecutorTask:
    return ExecutorTask(id="t1", goal="probe", target=url,
                        permission_required=PermissionMode.STANDARD_TESTING)


def _state() -> SessionState:
    state = SessionState()
    state.scope.target = "127.0.0.1"
    state.scope.in_scope = ["127.0.0.1"]
    state.scope.authorized = True
    state.scope.environment = "local"
    state.scope.rate_limit_rps = 50.0
    return state


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        if p.path == "/sqli":
            v = q.get("id", [""])[0]
            if "'" in v:
                self._send(500, b"you have an error in your sql syntax near '''")
            elif "AND 1=2" in v:
                self._send(200, b"MISMATCHED")
            elif "SLEEP" in v:
                self._send(200, b"ok")  # tanpa delay -> time-blind TIDAK false-positive
            else:
                self._send(200, b"DEBUG id=7 name=admin row=42 bytes=originbaselinevalue1234567890abcdefghij")
        elif p.path == "/ssti":
            v = q.get("name", [""])[0]
            rendered = v.replace("{{7*7}}", "49").replace("{{7*'7'}}", "7777777")
            self._send(200, f"hi {rendered}".encode())
        elif p.path == "/cmdi":
            v = q.get("host", [""])[0]
            if "id" in v:
                self._send(200, b"uid=1000(test) gid=1000(test) groups=1000(test)")
            else:
                self._send(200, b"lookup ok")
        elif p.path == "/trav":
            v = unquote(q.get("file", [""])[0])
            norm = os.path.normpath(v)
            if norm.endswith("/etc/passwd"):
                self._send(200, b"root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:1:daemon:/usr/sbin")
            else:
                self._send(404, b"not found")
        else:
            self._send(404, b"nf")


@pytest.fixture(scope="module")
def base_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_sqli_error_and_boolean_evidence(base_url):
    r = SqliExecutor(_state()).execute(_task(f"{base_url}/sqli?id=7"))
    assert r.status == "success"
    text = " | ".join(r.evidence)
    assert "error in your sql syntax" in text.lower()
    assert "boolean-blind" in text.lower()
    # Time-blind TIDAK menyala (server tak delay) — anti false-positive.
    assert not any("time-blind" in e.lower() for e in r.evidence)


def test_sqli_no_params_is_informational(base_url):
    r = SqliExecutor(_state()).execute(_task(f"{base_url}/cmdi"))
    assert r.status == "success"
    assert r.risk == "low"


def test_sssti_server_side_evaluation(base_url):
    r = SstiExecutor(_state()).execute(_task(f"{base_url}/ssti?name=x"))
    text = " | ".join(r.evidence)
    assert "server-side" in text.lower()
    assert "`49`" in text
    assert r.risk == "high"


def test_cmdi_uid_echo_is_critical(base_url):
    r = CmdiExecutor(_state()).execute(_task(f"{base_url}/cmdi?host=localhost"))
    assert r.risk == "critical"
    assert any("uid=" in e for e in r.evidence)


def test_traversal_passwd_marker(base_url):
    r = PathTraversalExecutor(_state()).execute(
        _task(f"{base_url}/trav?file=notes.txt"))
    text = " | ".join(r.evidence)
    assert "root:x:0:0:" in text
    assert r.risk == "high"


def test_traversal_without_param_or_path(base_url):
    r = PathTraversalExecutor(_state()).execute(_task(f"{base_url}/cmdi"))
    assert r.status == "success"
    assert r.risk == "low"


def test_nuclei_parse_synthetic_output(base_url):
    ex = NucleiExecutor(_state())
    ex._run_nuclei = lambda t: (0, "\n".join([
        '{"template-id":"CVE-1","info":{"name":"RCE","severity":"critical"},"matched-at":"%s/api","type":"http"}' % base_url,
        '{"template-id":"tech","info":{"name":"Tech","severity":"info"},"matched-at":"%s/","type":"http"}',
        'bukan-json',
    ]), "")
    r = ex.execute(_task(f"{base_url}/"))
    assert r.status == "success"
    assert r.risk == "critical"
    assert any("nuclei[CRITICAL]" in e for e in r.evidence)
    assert any("unparsed_lines=1" in o for o in r.observations)


def test_new_executors_registered():
    from wth_ai.executor.vuln import VULN_EXECUTORS

    for key in ("sqli", "ssti", "cmdi", "path_traversal", "lfi", "file_path",
                "nuclei", "command_injection", "sql_injection"):
        assert key in VULN_EXECUTORS
