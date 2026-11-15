"""Strix-mode agent tools: python_runtime, self-improve memory, auto-adaptive probe.

Semuanya nyata: python_runtime jalan di run_shell sungguhan, self_improve
menulis ke `wth-home / memory/agent_memory.json`, dan http_probe yang terblokir
otomatis memutar katalog mutasi adaptif tanpa perlu/menunggu perintah operator.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wth_ai.terminal.models import TerminalResult


CB = SimpleNamespace(**{k: None for k in (
    "on_phase_change", "on_tool_start", "on_tool_end", "on_approval_required",
    "on_llm_start", "on_llm_tick", "on_llm_end", "on_user", "on_response",
    "on_thinking", "on_subagent_start", "on_subagent_end", "on_job_progress",
)})


class _FakeTerminal:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def dispatch_tool(self, name, args, *, actor, approved=False):
        self.calls.append((name, dict(args)))
        if name == "http_probe":
            hdrs = {k.lower() for k in (args.get("headers") or {})}
            if "x-forwarded-for" in hdrs:
                return TerminalResult(
                    transcript_id="TX2", actor=actor, purpose="p", command="GET u",
                    stdout="admin ok", stderr="", exit_code=0,
                    url=str(args.get("url", "")), method=str(args.get("method", "GET")),
                    status_code=200, response_headers={"Content-Type": "text/html"},
                )
            return TerminalResult(
                transcript_id="TX1", actor=actor, purpose="p", command="GET u",
                stdout="request blocked", stderr="", exit_code=1,
                url=str(args.get("url", "")), method=str(args.get("method", "GET")),
                status_code=403, response_headers={"CF-Ray": "8f00", "Server": "cloudflare"},
            )
        # python_runtime -> run_shell heredoc: real shell, fake output
        return TerminalResult(
            transcript_id="TXS", actor=actor, purpose=str(args.get("purpose", "")),
            command=str(args.get("command", "run shell")),
            stdout="WTHPY-OK", stderr="", exit_code=0,
        )


def _ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    ctx = SimpleNamespace(
        config=SimpleNamespace(safety=SimpleNamespace(no_limits=False)),
        terminal=_FakeTerminal(),
        state=SimpleNamespace(metadata={}),
        callbacks=CB, tool_count=0, action_memory=None, persist_fn=None,
    )
    ctx.notify_tool_start = lambda name, args: setattr(ctx, "tool_count", ctx.tool_count + 1)
    ctx.notify_tool_end = lambda name, result, ms: None
    return ctx


def _dispatch(ctx, name, args):
    from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool

    return dispatch_supervisor_tool(ctx, name, args, approved=True)


def test_python_runtime_executes_via_run_shell_heredoc(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    out = _dispatch(ctx, "python_runtime", {"code": "print('WTHPY-OK')"})
    assert "WTHPY-OK" in out
    tool, payload = ctx.terminal.calls[-1][0], ctx.terminal.calls[-1][1]
    assert tool == "run_shell"
    assert payload["command"].startswith("python3 - <<'WTHPYEOF'")


def test_self_improve_persists_lesson(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    out = _dispatch(ctx, "self_improve", {
        "topic": "bypass", "lesson": "header_xff wins on cloudflare", "evidence": "TX1",
    })
    assert "LESSON TERSIMPAN" in out
    raw = json.loads((tmp_path / "memory" / "agent_memory.json").read_text())
    assert raw["lessons"] and raw["lessons"][0]["topic"] == "bypass"


def test_memory_write_then_read_roundtrip(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    _dispatch(ctx, "memory_write", {"key": "session_token", "value": "eyJ.abc"})
    out = _dispatch(ctx, "memory_read", {"key": "session_token"})
    assert "eyJ.abc" in out


def test_new_tools_registered_for_supervisor_actor():
    from wth_ai.agents.tool_registry import tools_for_actor

    names = {d["function"]["name"] for d in tools_for_actor("supervisor")}
    for tool in ("python_runtime", "self_improve", "memory_write", "memory_read"):
        assert tool in names, f"{tool} hilang dari registry"


def test_http_probe_auto_retries_with_adaptive_engine(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    out = _dispatch(ctx, "http_probe", {"method": "GET", "url": "https://example.com/admin"})
    assert "[ADAPTIF]" in out and "BYPASS" in out
    assert len(ctx.terminal.calls) >= 2  # 403 baseline + adaptive mutation(s)
    assert any("x-forwarded-for" in {k.lower() for k in (payload.get("headers") or {})}
               for _, payload in ctx.terminal.calls[1:])


def test_http_probe_adaptive_opt_out(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)
    out = _dispatch(ctx, "http_probe",
                    {"method": "GET", "url": "https://example.com/admin", "adaptive": "0"})
    assert "[ADAPTIF]" not in out
    assert len(ctx.terminal.calls) == 1


def test_live_http_probe_bypass_through_real_http(tmp_path, monkeypatch):
    from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
    import requests

    home = tmp_path
    monkeypatch.setenv("WTH_HOME", str(home))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("X-Forwarded-For") == "127.0.0.1":
                body = b"<html>admin dashboard</html>"
                self.send_response(200)
            else:
                body = b"request blocked"
                self.send_response(403)
                self.send_header("CF-Ray", "8f00")
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN003
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        class LiveBridge:
            def dispatch_tool(self, name, args, **kw):  # noqa: ANN003
                r = requests.request(args.get("method", "GET"), args.get("url"),
                                     headers=args.get("headers") or {}, timeout=5)
                return TerminalResult(
                    transcript_id=f"TX-R{r.status_code}", actor="supervisor", purpose="probe",
                    command=str(r.status_code), stdout=r.text, stderr="", exit_code=0,
                    url=args.get("url", ""), method=args.get("method", "GET"),
                    status_code=r.status_code, response_headers=dict(r.headers),
                )
        ctx = _ctx(home, monkeypatch)
        ctx.terminal = LiveBridge()
        out = dispatch_supervisor_tool(ctx, "http_probe",
                                       {"method": "GET", "url": f"http://127.0.0.1:{port}/admin"},
                                       approved=True)
        assert "[ADAPTIF]" in out and "status=200" in out and "BYPASS" in out
        assert (home / "adaptive").exists()
    finally:
        srv.shutdown()
