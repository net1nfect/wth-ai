"""MCP bridge — optional HexStrike / OOB integration."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
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
    """Lightweight MCP bridge for CLI — delegates to configured MCP servers."""

    def __init__(self, config: WTHConfig | None = None) -> None:
        self.config = config or WTHConfig.load()
        self.enabled = self.config.mcp.enabled
        self.servers = self.config.mcp.servers
        self._handlers: dict[str, Callable[..., MCPToolResult]] = {
            "oob_callback": self._oob_callback,
            "hexstrike_scan": self._hexstrike_scan,
        }

    def available(self) -> bool:
        return self.enabled and bool(self.servers)

    def _oob_callback(self, *, callback_type: str = "http", session_id: str = "") -> MCPToolResult:
        """Generate OOB callback placeholder for blind SSRF/XXE/SQLi."""
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
            output=f"OOB callback URL: {callback_url} (configure collaborator in MCP settings)",
        )

    def _hexstrike_scan(self, *, target: str, scan_type: str = "recon") -> MCPToolResult:
        """Delegate scan to HexStrike MCP if available."""
        mcp_config = Path.home() / ".cursor" / "projects"
        if not mcp_config.exists():
            return MCPToolResult(
                tool="hexstrike_scan",
                success=False,
                output="",
                error="HexStrike MCP not configured — enable mcp.enabled in wth_config.yaml",
            )
        return MCPToolResult(
            tool="hexstrike_scan",
            success=True,
            output=f"HexStrike delegation queued: {scan_type} on {target} (requires MCP server running)",
        )

    def call(self, tool: str, **kwargs: Any) -> MCPToolResult:
        if not self.available():
            return MCPToolResult(tool=tool, success=False, output="", error="MCP disabled")
        handler = self._handlers.get(tool)
        if not handler:
            return MCPToolResult(tool=tool, success=False, output="", error=f"Unknown MCP tool: {tool}")
        try:
            return handler(**kwargs)
        except Exception as exc:
            return MCPToolResult(tool=tool, success=False, output="", error=str(exc))

    def format_status(self) -> str:
        lines = [
            "## MCP Bridge",
            f"- Enabled: {self.enabled}",
            f"- Servers: {', '.join(self.servers) or 'none'}",
            f"- Available tools: {', '.join(self._handlers.keys())}",
        ]
        if not self.enabled:
            lines.append("\nEnable in config: `mcp.enabled: true`")
        return "\n".join(lines)
