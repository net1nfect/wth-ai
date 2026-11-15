"""Tool AI pipeline: surface, probe, scope/permission gate, dan bukti deterministik."""

from __future__ import annotations

import json
import threading
import requests
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode, SessionState
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.oob import get_oob_service, reset_oob_service
from wth_ai.terminal.bridge import AgentTerminalBridge


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path.startswith("/race"):
            body = b"redeemed"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            body = b"<html><title>INTERNAL-TOOL-MARKER</title><h1>Internal</h1></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/ssrf"):
            value = (parse_qs(urlparse(self.path).query).get("url") or [""])[0]
            if ":1/" in value:
                body = b"fetch error"
                self.send_response(502)
            else:
                body = b"INTERNAL-TOOL-MARKER"
                self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/external-graph":
            body = b'<html><script src="/app-graph.js"></script></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/app-graph.js":
            body = b"import './chunk.js'; fetch('/api/graph?q=1');"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/chunk.js":
            body = b"fetch('/api/chunk?x=1');"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/external":
            body = b'<html><script src="/app.js"></script></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/app.js"):
            body = b"fetch('/api/ext?q=1');"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/openapi.yaml"):
            body = b"""openapi: 3.0.0
servers: [{url: /api}]
paths:
  /yaml/users/{id}:
    get:
      parameters:
        - {name: id, in: path, schema: {type: integer}}
        - {name: expand, in: query, schema: {type: string, default: roles}}
"""
            self.send_response(200)
            self.send_header("Content-Type", "application/yaml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/openapi.json"):
            body = json.dumps({
                "openapi": "3.0.0",
                "servers": [{"url": "/api"}],
                "paths": {
                    "/users/{id}": {
                        "get": {"parameters": [
                            {"name": "id", "in": "path", "schema": {"type": "integer"}},
                        ]},
                    },
                    "/login": {
                        "post": {"requestBody": {"content": {"application/json": {
                            "schema": {"type": "object", "properties": {
                                "user": {"type": "string"}, "pass": {"type": "string"},
                            }},
                        }}}},
                    },
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/discover"):
            body = b""""<html><body>
            <form action="/search" method="get"><input name="q" value="seed"></form>
            <form action="/login" method="post"><input name="user" value="a"><input name="pass" value="b"></form>
            <a href="/api/user?id=9">u</a>
            <script>fetch('/api/items?sort=asc')</script>
            </body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/me"):
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                body = b'{"sub": 1, "role": "user"}'
                self.send_response(200)
            else:
                body = b"unauthorized"
                self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/user/"):
            uid = self.path.rsplit("/", 1)[-1]
            users = {
                "2": '{"id": 2, "email": "user2@test", "role": "user"}',
                "3": '{"id": 3, "email": "user3@test", "role": "user"}',
            }
            body = users.get(uid, "{}").encode()
            self.send_response(200 if uid in users else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/redirect"):
            target = "https://example.org/landing" if "example.org" in self.path else "/home"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/sqli"):
            value = self.path.split("id=", 1)[1] if "id=" in self.path else ""
            if "'" in value or "%27" in value.upper():
                body = b"you have an error in your sql syntax near quote"
                self.send_response(500)
            else:
                body = b"ok"
                self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"safe page"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _ctx(tmp_path, monkeypatch, base: str, *, mode: PermissionMode = PermissionMode.STANDARD_TESTING):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    config = WTHConfig.load()
    sm = ScopeManager()
    sm.set_scope(target=base, in_scope=[base], authorized=True, rate_limit_rps=100.0)
    pm = PermissionManager(mode)
    state = SessionState()
    state.scope = sm.scope
    terminal = AgentTerminalBridge(config, pm, sm, "test-pipeline-tools")
    ctx = SimpleNamespace(
        config=config, state=state, scope_mgr=sm, permission_mgr=pm,
        terminal=terminal, callbacks=None, tool_count=0, action_memory=None,
        persist_fn=None,
    )
    ctx.notify_tool_start = lambda name, args: setattr(ctx, "tool_count", ctx.tool_count + 1)
    ctx.notify_tool_end = lambda name, result, ms: None
    return ctx


def _dispatch(ctx, name, args):
    return dispatch_supervisor_tool(ctx, name, args, approved=True)


def test_pipeline_surface_lists_params_without_request(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_surface", {
        "target": f"{server}/api/login",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "body": '{"user":"admin","pass":"x"}',
    })
    data = json.loads(out)
    assert [p["name"] for p in data["params"]] == ["user", "pass"]
    assert all(p["location"] == "json" for p in data["params"])
    assert ctx.terminal.transcripts.count() == 0, "surface tidak boleh mengirim request"


def test_pipeline_probe_returns_verified_evidence(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "sqli",
        "target": f"{server}/sqli?id=7",
        "max_probes": 3,
    })
    data = json.loads(out)
    assert data["verified"] is True
    assert any("error in your sql syntax" in e.lower() for e in data["evidence"])
    assert data["probes"], "harus ada probe yang tercatat"
    assert all(p.get("transcript_id") for p in data["probes"])
    pairs = ctx.state.metadata.get("request_pairs", [])
    assert len(pairs) >= 2, "baseline + probe harus jadi pasangan bukti"


def test_pipeline_probe_blocks_out_of_scope(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "sqli", "target": "http://evil-outside.test/x?id=1",
    })
    assert "out of scope" in out.lower()
    assert ctx.terminal.transcripts.count() == 0


def test_pipeline_probe_blocks_read_only_mode(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server, mode=PermissionMode.READ_ONLY_RECON)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "sqli", "target": f"{server}/sqli?id=7",
    })
    assert "ditolak" in out.lower()
    assert ctx.terminal.transcripts.count() == 0


def test_pipeline_probe_without_params_is_not_a_finding(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "sqli", "target": f"{server}/nothing",
    })
    data = json.loads(out)
    assert data["verified"] is False
    assert "tidak ada parameter" in data["message"].lower()
    assert ctx.terminal.transcripts.count() == 0


def test_pipeline_probe_open_redirect_reads_location_without_following(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "open_redirect",
        "target": f"{server}/redirect?next=/home",
    })
    data = json.loads(out)
    assert data["verified"] is True
    assert any("location eksternal" in e.lower() for e in data["evidence"]), data
    assert data["probes"][0]["status"] == 302


def test_pipeline_probe_idor_path_only_without_query(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "idor", "target": f"{server}/api/user/2",
    })
    data = json.loads(out)
    assert data["params"] == [], "IDOR path-only tidak butuh query param"
    assert data["verified"] is True
    assert any("tanpa otorisasi" in e.lower() for e in data["evidence"]), data


def test_pipeline_probe_auth_alg_none_forgery(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    ctx.state.metadata["captured_jwt"] = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxIiwicm9sZSI6InVzZXIifQ."
        "c2ln"
    )
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "auth", "target": f"{server}/api/me",
    })
    data = json.loads(out)
    assert data["verified"] is True
    assert any("forged token accepted" in e.lower() for e in data["evidence"]), data


def test_pipeline_discover_extracts_forms_links_and_js(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_discover", {"target": f"{server}/discover"})
    data = json.loads(out)
    assert data["baseline_status"] == 200
    assert data["count"] >= 4
    by_key = {(x["method"], x["url"]) for x in data["surfaces"]}
    assert ("GET", f"{server}/search?q=seed") in by_key
    assert ("POST", f"{server}/login") in by_key
    assert ("GET", f"{server}/api/items?sort=asc") in by_key
    assert ctx.terminal.transcripts.count() >= 1, "seed fetch harus teraudit"


def test_pipeline_discover_reads_openapi_json(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_discover", {"target": f"{server}/openapi.json"})
    data = json.loads(out)
    by_key = {(x["method"], x["url"]): x for x in data["surfaces"]}
    assert ("GET", f"{server}/api/users/1") in by_key
    login = by_key[("POST", f"{server}/api/login")]
    assert [p["name"] for p in login["params"]] == ["user", "pass"]


def test_pipeline_probe_race_burst_is_parallel_and_verified(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "race", "target": f"{server}/race", "max_probes": 5,
    })
    data = json.loads(out)
    assert data["verified"] is True, data
    assert any("paralel mengembalikan 2xx" in e for e in data["evidence"]), data
    assert len(data["probes"]) == 5


def test_pipeline_probe_ssrf_proves_internal_content(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_probe", {
        "vuln_class": "ssrf", "target": f"{server}/ssrf?url=x",
    })
    data = json.loads(out)
    assert data["verified"] is True, data
    assert any("TERBUKTI" in e for e in data["evidence"]), data
    assert any("INTERNAL-TOOL-MARKER" in e for e in data["evidence"]), data


def test_pipeline_discover_fetches_external_same_origin_js(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_discover", {"target": f"{server}/external"})
    data = json.loads(out)
    assert data["external_scripts_fetched"] == 1, data
    by_key = {(x["method"], x["url"]) for x in data["surfaces"]}
    assert ("GET", f"{server}/api/ext?q=1") in by_key


def test_pipeline_discover_reads_openapi_yaml(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_discover", {"target": f"{server}/openapi.yaml"})
    data = json.loads(out)
    by_key = {(x["method"], x["url"]): x for x in data["surfaces"]}
    assert ("GET", f"{server}/api/yaml/users/1?expand=roles") in by_key


class _BlindHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/blind"):
            value = (parse_qs(urlparse(self.path).query).get("url") or [""])[0]
            if "127.0.0.1:1/" in value:
                body = b"fetch error"
                self.send_response(500)
            else:
                try:
                    body = requests.get(value, timeout=3).text.encode()
                    self.send_response(200)
                except Exception:  # noqa: BLE001
                    body = b"fetch error"
                    self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_pipeline_probe_blind_ssrf_oob_writes_transcript(tmp_path, monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _BlindHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    ctx = _ctx(tmp_path, monkeypatch, base)
    reset_oob_service()
    svc = get_oob_service()
    svc.start(port=0)
    try:
        out = _dispatch(ctx, "pipeline_probe", {
            "vuln_class": "ssrf", "target": f"{base}/blind?url=x",
        })
        data = json.loads(out)
        assert data["verified"] is True, data
        assert any("TERBUKTI (OOB)" in e for e in data["evidence"]), data
        assert any("[TX:" in e for e in data["evidence"]), data
        assert ctx.terminal.transcripts.count() >= 1
    finally:
        httpd.shutdown()
        reset_oob_service()


def test_pipeline_discover_follows_same_origin_js_imports(tmp_path, monkeypatch, server):
    ctx = _ctx(tmp_path, monkeypatch, server)
    out = _dispatch(ctx, "pipeline_discover", {"target": f"{server}/external-graph"})
    data = json.loads(out)
    assert data["external_scripts_fetched"] == 2, data
    assert data["imported_js_fetched"] == 1, data
    by_key = {(x["method"], x["url"]) for x in data["surfaces"]}
    assert ("GET", f"{server}/api/graph?q=1") in by_key
    assert ("GET", f"{server}/api/chunk?x=1") in by_key
