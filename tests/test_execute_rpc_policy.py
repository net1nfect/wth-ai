"""Execute RPC policy and redaction tests."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from wth_ai.rpc.execute_server import (
    ExecuteRpcServer,
    _parse_timeout,
    redact_secrets,
)


def test_redact_secrets():
    raw = "api_key=sk-live-abcdef token=abc123"
    out = redact_secrets(raw)
    assert "sk-live" not in out
    assert "REDACTED" in out


def test_execute_blocked_out_of_scope():
    srv = ExecuteRpcServer()
    result = srv.execute({"target": "", "code": "print(1)"})
    assert result["status"] == "blocked"


def test_parse_timeout_rejects_non_numeric():
    with pytest.raises(ValueError):
        _parse_timeout("abc")

def test_parse_timeout_rejects_zero_and_negative():
    for bad in (0, -1, "0"):
        with pytest.raises(ValueError):
            _parse_timeout(bad)

def test_parse_timeout_clamps_to_maximum():
    assert _parse_timeout(99999) == 300
    assert _parse_timeout(None) == 30

def test_execute_blocks_invalid_timeout():
    srv = ExecuteRpcServer()
    srv.scope.is_in_scope = lambda target: (True, "")
    srv.perms.check_command = lambda *a, **k: {"allowed": True}
    out = srv.execute({"target": "example.com", "code": "print(1)", "timeout": "abc"})
    assert out["status"] == "blocked"
    assert "timeout" in out["error"]

def test_execute_blocked_permission():
    srv = ExecuteRpcServer()
    srv.scope.set_scope(target="example.com", authorized=True, in_scope=["example.com"])
    ok, _ = srv.scope.is_in_scope("example.com")
    assert ok
    result = srv.execute({"target": "example.com", "code": "print(1)"})
    assert result["status"] == "blocked"


def _granted_server() -> ExecuteRpcServer:
    srv = ExecuteRpcServer()
    srv.scope.is_in_scope = lambda target: (True, "")
    srv.perms.check_command = lambda *a, **k: {"allowed": True}
    return srv


def test_execute_blocks_without_docker(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    out = _granted_server().execute({"target": "example.com", "code": "print(1)"})
    assert out["status"] == "blocked"
    assert "sandbox" in out["error"]


def test_execute_uses_cap_drop_and_internal_network(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(ExecuteRpcServer, "_network_is_internal", lambda self: True)
    captured = {}

    class Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = _granted_server().execute({"target": "example.com", "code": "print(1)"})
    assert out["status"] == "success"
    for flag in ("--cap-drop", "ALL", "--read-only", "--network"):
        assert flag in captured["argv"]
    assert "/work/run.py" in captured["argv"]


def test_execute_rejects_non_object_payload():
    from typing import Any, cast

    out = ExecuteRpcServer().execute(cast(Any, [1, 2]))
    assert out["status"] == "blocked"


def test_execute_blocks_when_network_not_internal(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(ExecuteRpcServer, "_network_is_internal", lambda self: False)
    out = _granted_server().execute({"target": "example.com", "code": "print(1)"})
    assert out["status"] == "blocked"
    assert "internal" in out["error"]


def test_timeout_removes_container(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(ExecuteRpcServer, "_network_is_internal", lambda self: True)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if "run" in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = _granted_server().execute({"target": "example.com", "code": "print(1)"})
    assert out["status"] == "timeout"
    assert any("rm" in c and "-f" in c for c in calls)
