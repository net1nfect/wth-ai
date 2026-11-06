"""Gateway daemon, RPC, and session cache tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wth_ai.gateway.client import GatewayClient
from wth_ai.gateway.rpc import GatewayRpcServer
from wth_ai.gateway.session_cache import SessionCache


def _dispatch(method: str, params: dict, peer=None):
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


def test_session_owner_allowed_helper():
    """P2-2: helper di daemon menerima pemilik yang cocok, menolak yang beda."""
    import os
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from wth_ai.config import WTHConfig
    from wth_ai.gateway.daemon import _session_owner_allowed
    from wth_ai.gateway.session_cache import SessionCache

    with TemporaryDirectory() as td, patch.dict(os.environ, {"WTH_HOME": td}):
        cache = SessionCache(WTHConfig.load())
        assert _session_owner_allowed(cache, "k", {"pid": 100}) is True
        assert _session_owner_allowed(cache, "k", {"pid": 100}) is True
        assert _session_owner_allowed(cache, "k", {"pid": 200}) is False
        # peer tanpa pid (tidak tersedia) tetap diizinkan
        assert _session_owner_allowed(cache, "other", None) is True


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


def test_session_cache_first_touch_ownership(monkeypatch, tmp_path):
    """P2-2: pemanggil pertama sebuah session_key menjadi pemiliknya; pid lain
    ditolak menyentuhnya (anti-spoof cross-proses)."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.config import WTHConfig
    cache = SessionCache(WTHConfig.load())
    assert cache.claim("cli:default", 100) is True
    assert cache.owner_of("cli:default") == 100
    assert cache.claim("cli:default", 200) is False
    assert cache.owner_of("cli:default") == 100
    assert cache.claim("cli:default", 100) is True


def test_session_cache_release_clears_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.config import WTHConfig
    cache = SessionCache(WTHConfig.load())
    cache.claim("k", 100)
    cache.release("k")
    assert cache.owner_of("k") is None
    assert cache.claim("k", 200) is True

def test_oversized_request_is_rejected_and_closed(tmp_path):
    import json
    import socket
    import threading

    from wth_ai.gateway.rpc import MAX_LINE_BYTES

    srv = GatewayRpcServer(tmp_path / "gw.sock", _dispatch)
    a, b = socket.socketpair()
    worker = threading.Thread(target=srv._handle_conn, args=(b,), daemon=True)
    worker.start()
    try:
        a.sendall(b"x" * (MAX_LINE_BYTES + 1))
        a.settimeout(10)
        assert json.loads(a.recv(4096).decode())["error"]["code"] == -32001
        assert a.recv(1) == b""
    finally:
        a.close()
        worker.join(timeout=10)


def test_small_requests_on_one_connection_still_work(tmp_path):
    import json
    import socket
    import threading

    srv = GatewayRpcServer(tmp_path / "gw.sock", _dispatch)
    a, b = socket.socketpair()
    worker = threading.Thread(target=srv._handle_conn, args=(b,), daemon=True)
    worker.start()
    try:
        a.sendall((json.dumps({"id": 1, "method": "ping"}) + "\n" +
                   json.dumps({"id": 2, "method": "ping"}) + "\n").encode())
        a.shutdown(socket.SHUT_WR)
        a.settimeout(10)
        data = b""
        while True:
            chunk = a.recv(4096)
            if not chunk:
                break
            data += chunk
        replies = [json.loads(line) for line in data.splitlines()]
        assert [r["id"] for r in replies] == [1, 2]
    finally:
        a.close()
        worker.join(timeout=10)
