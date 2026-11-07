"""Transport MCP HTTP: bearer dari vault, dan egress tetap diperiksa."""

from __future__ import annotations

import json
from typing import Any

import pytest

from wth_ai.mcp.config import MCPServerSpec
from wth_ai.mcp.manager import MCPClientManager, _HttpMCPConnection
from wth_ai.netguard import EgressDenied


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.status = 200

    def read(self) -> bytes:
        return self._body


class _FakeConn:
    """Koneksi palsu yang mencatat request; balasan dari `responder`."""

    last: _FakeConn | None = None
    responder: Any = None

    def __init__(self, host: str, port: int | None = None,
                 timeout: float | None = None) -> None:
        _FakeConn.last = self
        self.host = host
        self.port = port
        self.method = ""
        self.path = ""
        self.payload: dict[str, Any] = {}
        self.headers: dict[str, str] = {}

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict[str, str] | None = None) -> None:
        self.method = method
        self.path = path
        self.payload = json.loads((body or b"{}").decode())
        self.headers = dict(headers or {})

    def getresponse(self) -> _FakeResponse:
        result = _FakeConn.responder(self.payload)
        body = {"jsonrpc": "2.0", "id": self.payload["id"], "result": result}
        return _FakeResponse(json.dumps(body).encode())

    def close(self) -> None:
        return None


def _spec(url: str = "http://127.0.0.1:9999/mcp") -> MCPServerSpec:
    return MCPServerSpec(
        transport="http",
        url=url,
        headers={"Authorization": "Bearer uji"},
    )


def _install(monkeypatch: pytest.MonkeyPatch, responder: Any) -> None:
    _FakeConn.last = None
    _FakeConn.responder = responder
    monkeypatch.setattr("wth_ai.mcp.manager.http.client.HTTPConnection", _FakeConn)
    monkeypatch.setattr("wth_ai.mcp.manager.http.client.HTTPSConnection", _FakeConn)


def test_http_connection_lists_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda _payload: {"tools": [{"name": "echo"}]})
    conn = _HttpMCPConnection("srv", _spec())

    assert conn.start() is True
    result = conn._rpc("tools/list", {})

    assert result["tools"][0]["name"] == "echo"
    fake = _FakeConn.last
    assert fake is not None
    assert fake.method == "POST"
    assert fake.path == "/mcp"
    assert (fake.host, fake.port) == ("127.0.0.1", 9999)
    assert fake.payload["method"] == "tools/list"
    assert fake.headers["Content-Type"] == "application/json"


def test_headers_membawa_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda _payload: {"tools": []})
    conn = _HttpMCPConnection("srv", _spec())
    conn._rpc("tools/list", {})

    fake = _FakeConn.last
    assert fake is not None
    assert fake.headers["Authorization"] == "Bearer uji"


def test_http_connection_refuses_host_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda _payload: {"tools": []})
    conn = _HttpMCPConnection("srv", _spec("https://evil.example/mcp"))

    assert conn.start() is False
    with pytest.raises(EgressDenied):
        conn._rpc("tools/list", {})
    # Guard jalan sebelum socket: tidak ada koneksi yang dibuat.
    assert _FakeConn.last is None


def test_http_connection_call_tool_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        lambda _payload: {"content": [{"type": "text", "text": "halo"}]},
    )
    conn = _HttpMCPConnection("srv", _spec())

    result = conn.call_tool("echo", {"pesan": "halo"})

    assert result.success is True
    assert result.output == "halo"


def test_connect_all_mendaftarkan_transport_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    manifest = tmp_path / "mcp.json"
    manifest.write_text(json.dumps({"servers": {
        "srv": {"transport": "http", "url": "http://127.0.0.1:9999/mcp"},
    }}))
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    monkeypatch.setenv("WTH_MCP_MANIFEST", str(manifest))
    _install(monkeypatch, lambda _payload: {"tools": []})

    manager = MCPClientManager()
    status = manager.connect_all()

    # Manifest ikut digabung dengan `mcp.servers` di config, jadi cukup
    # pastikan server http kita terdaftar dan tersambung.
    assert status["srv"] is True
    assert "srv" in manager.list_tools()
