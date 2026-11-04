"""Adapter tampilan MCP: memakai MCPClientManager repo."""

from __future__ import annotations

from wth_ai.config import WTHConfig
from wth_ai.mcp.manager import MCPClientManager


def mcp_summary(config: WTHConfig | None = None) -> str:
    manager = MCPClientManager(config or WTHConfig.load())
    return manager.format_status()