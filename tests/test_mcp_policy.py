"""MCP policy enforcement: `allowed_tools` allowlist + rate-limit.

Covers P2-3 (audit 2026-11-06): `MCPConfig.policy.allowed_tools` dan
`max_calls_per_minute` didefinisikan di config tapi sebelumnya tidak pernah
ditegakkan di `MCPClientManager.call_tool`. Di sini kita verifikasi deny
allowlist dan rate-limit dipakai sebelum transport, dan `*`/0 berarti tak
terbatas.
"""

from __future__ import annotations

from wth_ai.config import WTHConfig
from wth_ai.mcp.manager import MCPClientManager, MCPToolResult as _Result


class _FakeConn:
    """Connection palsu yang selalu sukses — untuk menguji policy, bukan
    transport."""

    def __init__(self):
        self.calls = []
        self.tools = []

    def call_tool(self, tool, arguments):
        self.calls.append(tool)
        return _Result(tool=tool, success=True, output="ok", error="")


def _manager(tmp_path, monkeypatch, calls=None, tools=None):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    cfg = WTHConfig.load()
    cfg.mcp.servers = {"stub": {"command": "true", "args": []}}
    m = MCPClientManager(cfg)
    conn = calls if calls is not None else _FakeConn()
    if tools is not None:
        conn.tools = tools
    m._connections["stub"] = conn
    return m, conn


def test_allowed_tools_denies_tool_outside_allowlist(tmp_path, monkeypatch):
    m, conn = _manager(tmp_path, monkeypatch)
    m.config.mcp.policy["allowed_tools"] = ["ping"]
    res = m.call_tool("stub", "echo", {"message": "hi"}, mode=None)
    assert res.success is False
    assert "tidak diizinkan" in res.error
    assert conn.calls == []


def test_allowed_tools_allows_members(tmp_path, monkeypatch):
    m, conn = _manager(tmp_path, monkeypatch)
    m.config.mcp.policy["allowed_tools"] = ["ping", "echo"]
    res = m.call_tool("stub", "echo", {"message": "hi"}, mode=None)
    assert res.success is True
    assert conn.calls == ["echo"]


def test_allowed_tools_star_allows_everything(tmp_path, monkeypatch):
    m, conn = _manager(tmp_path, monkeypatch)
    m.config.mcp.policy["allowed_tools"] = ["*"]
    assert m.call_tool("stub", "anything", {}, mode=None).success is True
    assert conn.calls == ["anything"]


def test_max_calls_rate_limits_per_minute(tmp_path, monkeypatch):
    m, conn = _manager(tmp_path, monkeypatch)
    m.config.mcp.policy["allowed_tools"] = ["*"]
    m.config.mcp.policy["max_calls_per_minute"] = 2
    assert m.call_tool("stub", "a", {}, mode=None).success is True
    assert m.call_tool("stub", "b", {}, mode=None).success is True
    third = m.call_tool("stub", "c", {}, mode=None)
    assert third.success is False
    assert "rate" in third.error.lower() or "batas" in third.error
    assert len(conn.calls) == 2


def test_max_calls_zero_means_unlimited(tmp_path, monkeypatch):
    m, conn = _manager(tmp_path, monkeypatch)
    m.config.mcp.policy["allowed_tools"] = ["*"]
    m.config.mcp.policy["max_calls_per_minute"] = 0
    for _ in range(5):
        assert m.call_tool("stub", "a", {}, mode=None).success is True
    assert len(conn.calls) == 5
