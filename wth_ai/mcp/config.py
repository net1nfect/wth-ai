"""MCP server configuration models."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_NAME = "mcp.json"


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
        if isinstance(spec, MCPServerSpec):
            servers[name] = spec
        elif isinstance(spec, str):
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


def load_manifest(path: Path | None) -> dict[str, MCPServerSpec]:
    """Baca `mcp.json` (atau `mcpServers` gaya Claude Desktop).

    File tidak ada atau rusak → `{}`. Manifest adalah tambahan, bukan pengganti:
    server dari config YAML tetap menang bila namanya sama.
    """
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("servers") or data.get("mcpServers") or {}
    if not isinstance(raw, dict):
        return {}
    return parse_mcp_servers(raw)


def merge_manifest(yaml_servers: dict[str, Any],
                   manifest: dict[str, MCPServerSpec]) -> dict[str, Any]:
    """Config YAML menang; manifest hanya mengisi nama yang belum ada."""
    merged: dict[str, Any] = dict(manifest)
    merged.update(yaml_servers or {})
    return merged
