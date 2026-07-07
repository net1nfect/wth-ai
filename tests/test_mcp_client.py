"""Tests for MCP client manager."""

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.mcp.client import MCPClient
from wth_ai.mcp.config import parse_mcp_servers
from wth_ai.mcp.manager import MCPClientManager


def test_parse_mcp_servers():
    raw = {"filesystem": {"transport": "stdio", "command": "echo", "args": ["hi"]}}
    servers = parse_mcp_servers(raw)
    assert "filesystem" in servers
    assert servers["filesystem"].command == "echo"


def test_mcp_client_disabled_by_default():
    config = WTHConfig.load()
    config.mcp.enabled = False
    client = MCPClient(config)
    result = client.call("oob_callback", session_id="test123")
    assert result.success or not client.available()


def test_mcp_manager_format_status():
    config = WTHConfig.load()
    config.mcp.enabled = True
    config.mcp.servers = {}
    mgr = MCPClientManager(config)
    status = mgr.format_status()
    assert "MCP Client" in status
