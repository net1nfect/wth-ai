"""Execute RPC policy and redaction tests."""

from __future__ import annotations

import pytest

from wth_ai.rpc.execute_server import ExecuteRpcServer, redact_secrets


def test_redact_secrets():
    raw = "api_key=sk-live-abcdef token=abc123"
    out = redact_secrets(raw)
    assert "sk-live" not in out
    assert "REDACTED" in out


def test_execute_blocked_out_of_scope():
    srv = ExecuteRpcServer()
    result = srv.execute({"target": "", "code": "print(1)"})
    assert result["status"] == "blocked"


def test_execute_blocked_permission():
    srv = ExecuteRpcServer()
    srv.scope.set_scope(target="example.com", authorized=True, in_scope=["example.com"])
    ok, _ = srv.scope.is_in_scope("example.com")
    assert ok
    result = srv.execute({"target": "example.com", "code": "print(1)"})
    assert result["status"] == "blocked"
