"""MCP server configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPServerSpec:
    transport: str = "stdio"  # stdio | sse
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None


@dataclass
class MCPPolicyConfig:
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    max_calls_per_minute: int = 0
    audit_log: bool = True


def parse_mcp_servers(raw: dict[str, Any]) -> dict[str, MCPServerSpec]:
    servers: dict[str, MCPServerSpec] = {}
    for name, spec in (raw or {}).items():
        if isinstance(spec, str):
            servers[name] = MCPServerSpec(command=spec)
        elif isinstance(spec, dict):
            servers[name] = MCPServerSpec(
                transport=spec.get("transport", "stdio"),
                command=spec.get("command", ""),
                args=list(spec.get("args", [])),
                env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
                url=spec.get("url", ""),
                headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
                cwd=spec.get("cwd"),
            )
    return servers
