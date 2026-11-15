"""Executor auto-discovery: target berupa halaman form, bukan endpoint berparameter."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlparse

from wth_ai.executor.vuln.sqli import SqliExecutor
from wth_ai.models import ExecutorTask, PermissionMode, SessionState


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body: str):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/openapi.json":
            spec = {
                "openapi": "3.0.0",
                "servers": [{"url": "/"}],
                "paths": {"/search": {"get": {"parameters": [
                    {"name": "q", "in": "query", "schema": {"type": "string"}},
                ]}}},
            }
            raw = json.dumps(spec).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif parsed.path == "/external-graph":
            self._send(200, '<html><script src="/app.js"></script></html>')
        elif parsed.path == "/chunk.js":
            raw = b"fetch('/search?q=chunk');"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif parsed.path == "/external":
            self._send(200, '<html><script src="/app.js"></script></html>')
        elif parsed.path == "/app.js":
            raw = b"import './chunk.js'; fetch('/search?q=seed');"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif parsed.path == "/form":
            self._send(200, '<form action="/search" method="get"><input name="q" value="seed"></form>')
        elif parsed.path == "/search":
            value = (parse_qs(parsed.query).get("q") or [""])[0]
            if "'" in value:
                self._send(500, "you have an error in your sql syntax near quote")
            else:
                self._send(200, "ok")
        else:
            self._send(404, "nf")


def _state(base: str) -> SessionState:
    state = SessionState()
    state.scope.target = base
    state.scope.in_scope = ["127.0.0.1", base]
    state.scope.authorized = True
    state.scope.rate_limit_rps = 100.0
    return state


def test_executor_discovers_a_form_and_probes_its_endpoint():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        task = ExecutorTask(
            id="disc1", goal="sqli form", target=f"{base}/form",
            permission_required=PermissionMode.STANDARD_TESTING,
        )
        result = SqliExecutor(_state(base)).execute(task)
        assert result.status == "success"
        assert result.evidence, result
        assert any("discovered=form" in o for o in result.observations), result.observations
        assert any("parameter=q" in o for o in result.observations), result.observations
        assert result.risk == "high"
    finally:
        httpd.shutdown()


def test_executor_discovers_openapi_spec_when_seed_is_json():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        task = ExecutorTask(
            id="disc2", goal="sqli openapi", target=f"{base}/openapi.json",
            permission_required=PermissionMode.STANDARD_TESTING,
        )
        result = SqliExecutor(_state(base)).execute(task)
        assert result.status == "success"
        assert result.evidence, result
        assert any("discovered=openapi" in o for o in result.observations), result.observations
        assert result.risk == "high"
    finally:
        httpd.shutdown()


def test_executor_discovers_external_same_origin_js_routes():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        task = ExecutorTask(
            id="disc3", goal="sqli external js", target=f"{base}/external",
            permission_required=PermissionMode.STANDARD_TESTING,
        )
        result = SqliExecutor(_state(base)).execute(task)
        assert result.status == "success"
        assert result.evidence, result
        assert any("discovered=js:GET" in o for o in result.observations), result.observations
        assert result.risk == "high"
    finally:
        httpd.shutdown()


def test_executor_follows_same_origin_js_import_graph():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        task = ExecutorTask(
            id="disc4", goal="sqli import graph", target=f"{base}/external-graph",
            permission_required=PermissionMode.STANDARD_TESTING,
        )
        result = SqliExecutor(_state(base)).execute(task)
        assert result.status == "success"
        assert result.evidence, result
        assert any("discovered=js:GET" in o for o in result.observations), result.observations
        assert result.risk == "high"
    finally:
        httpd.shutdown()
