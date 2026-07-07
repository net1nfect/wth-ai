"""MCP bridge — delegates to MCPClientManager when enabled."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from wth_ai.config import WTHConfig


@dataclass
class MCPToolResult:
    tool: str
    success: bool
    output: str
    error: str = ""


class MCPClient:
    """Lightweight MCP bridge — uses MCPClientManager for real servers."""

    def __init__(self, config: WTHConfig | None = None) -> None:
        self.config = config or WTHConfig.load()
        self.enabled = self.config.mcp.enabled
        self.servers = list(self.config.mcp.servers.keys()) if isinstance(self.config.mcp.servers, dict) else []
        self._manager: Any = None
        self._handlers: dict[str, Callable[..., MCPToolResult]] = {
            "oob_callback": self._oob_callback,
        }
        if self.enabled:
            try:
                from wth_ai.mcp.manager import MCPClientManager
                self._manager = MCPClientManager(self.config)
                self._manager.connect_all()
            except Exception:  # noqa: BLE001
                self._manager = None

    def available(self) -> bool:
        return self.enabled and (bool(self.servers) or self._manager is not None)

    def _oob_callback(self, *, callback_type: str = "http", session_id: str = "") -> MCPToolResult:
        home = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
        collab_file = home / "data" / "oob_sessions.json"
        sessions: dict[str, Any] = {}
        if collab_file.exists():
            try:
                sessions = json.loads(collab_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                sessions = {}
        token = session_id or os.urandom(8).hex()
        callback_url = f"https://oob.wth-ai.local/{token}"
        sessions[token] = {"type": callback_type, "created": True}
        collab_file.parent.mkdir(parents=True, exist_ok=True)
        collab_file.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
        return MCPToolResult(
            tool="oob_callback",
            success=True,
            output=f"OOB callback URL: {callback_url}",
        )

    def call(self, tool: str, **kwargs: Any) -> MCPToolResult:
        if not self.available():
            return MCPToolResult(tool=tool, success=False, output="", error="MCP disabled")
        if ":" in tool and self._manager:
            server, tname = tool.split(":", 1)
            return self._manager.call_tool(server, tname, kwargs)
        handler = self._handlers.get(tool)
        if not handler:
            return MCPToolResult(tool=tool, success=False, output="", error=f"Unknown MCP tool: {tool}")
        try:
            return handler(**kwargs)
        except Exception as exc:
            return MCPToolResult(tool=tool, success=False, output="", error=str(exc))

    def format_status(self) -> str:
        if self._manager:
            return self._manager.format_status()
        lines = [
            "## MCP Bridge",
            f"- Enabled: {self.enabled}",
            f"- Servers: {', '.join(self.servers) or 'none'}",
            f"- Builtin tools: {', '.join(self._handlers.keys())}",
        ]
        return "\n".join(lines)

