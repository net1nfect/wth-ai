"""Gateway daemon, RPC, and session cache tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wth_ai.gateway.client import GatewayClient
from wth_ai.gateway.rpc import GatewayRpcServer
from wth_ai.gateway.session_cache import SessionCache


def _dispatch(method: str, params: dict):
    if method == "ping":
        return True
    if method == "handle_input":
        return f"ok:{params.get('text', '')}"
    if method == "get_status":
        return {"session_id": "s1", "target": "example.com", "permission": "read_only_recon", "phase": "idle"}
    if method == "gateway_status":
        return {"sessions": {"count": 1, "max": 128}, "worker": {"running": True}}
    if method == "list_sessions":
        return ["cli:default"]
    raise ValueError(method)


@pytest.fixture()
def gateway_sock(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    sock = Path("/tmp") / f"wth-gw-{tmp_path.name[:8]}.sock"
    if sock.exists():
        sock.unlink()
    server = GatewayRpcServer(sock, _dispatch)
    server.start()
    time.sleep(0.15)
    yield sock
    server.stop()


def test_gateway_rpc_ping(gateway_sock):
    client = GatewayClient(socket_path=gateway_sock)
    assert client.ping() is True


def test_gateway_rpc_handle_input(gateway_sock):
    client = GatewayClient(session_key="test:1", socket_path=gateway_sock)
    assert client.handle_input("/help") == "ok:/help"


def test_gateway_rpc_status(gateway_sock):
    client = GatewayClient(socket_path=gateway_sock)
    st = client.get_status()
    assert st["target"] == "example.com"
    gw = client.gateway_status()
    assert gw["worker"]["running"] is True


def test_session_cache_lru(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.config import WTHConfig
    cache = SessionCache(WTHConfig.load(), max_sessions=2, idle_ttl_seconds=3600)
    cache.get("a")
    cache.get("b")
    cache.get("c")
    keys = cache.list_keys()
    assert len(keys) == 2
    assert "a" not in keys
