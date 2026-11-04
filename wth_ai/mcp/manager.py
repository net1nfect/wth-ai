"""MCP client manager — connect to external MCP servers via stdio/SSE."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.mcp.client import MCPToolResult
from wth_ai.mcp.config import (MANIFEST_NAME, MCPServerSpec, load_manifest,
                               merge_manifest, parse_mcp_servers)
from wth_ai.vault import SecretVault, is_vault_ref, mask_secret


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    vault = SecretVault()
    for k, v in headers.items():
        if is_vault_ref(v):
            resolved = vault.resolve(v)
            out[k] = resolved or ""
        else:
            out[k] = v
    return out


class _StdioMCPConnection:
    """Minimal MCP stdio client using JSON-RPC newline protocol."""

    def __init__(self, name: str, spec: MCPServerSpec) -> None:
        self.name = name
        self.spec = spec
        self.proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._req_id = 0
        self.tools: list[dict[str, Any]] = []

    def start(self) -> bool:
        if not self.spec.command:
            return False
        env = {**os.environ, **self.spec.env}
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                [self.spec.command, *self.spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=self.spec.cwd,
            )
            self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "wth-ai", "version": "1.0"}})
            listed = self._rpc("tools/list", {})
            self.tools = listed.get("tools", []) if isinstance(listed, dict) else []
            return True
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TimeoutError):
            self.stop()
            return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.proc = None

    def _rpc(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> Any:
        if not self.proc or not self.proc.stdin or not self.proc.stdout:
            raise RuntimeError("MCP process not running")
        with self._lock:
            self._req_id += 1
            rid = self._req_id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise RuntimeError(str(msg["error"]))
                    return msg.get("result")
            raise TimeoutError(f"MCP RPC timeout: {method}")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        try:
            result = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
            content = result.get("content", []) if isinstance(result, dict) else []
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            output = "\n".join(text_parts) or json.dumps(result, default=str)
            return MCPToolResult(tool=tool_name, success=True, output=output[:200_000])
        except Exception as exc:  # noqa: BLE001
            return MCPToolResult(tool=tool_name, success=False, output="", error=str(exc))


class MCPClientManager:
    """Manages connections to configured MCP servers."""

    def __init__(self, config: WTHConfig | None = None) -> None:
        self.config = config or WTHConfig.load()
        self.enabled = self.config.mcp.enabled
        manifest_path = Path(os.environ.get("WTH_MCP_MANIFEST",
                                            Path.cwd() / MANIFEST_NAME))
        self.manifest_path = manifest_path
        manifest = load_manifest(manifest_path)
        self.servers = parse_mcp_servers(
            merge_manifest(self.config.mcp.servers, manifest))
        self._connections: dict[str, _StdioMCPConnection] = {}
        self._audit_path = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "logs" / "mcp_audit.jsonl"

    def connect_all(self) -> dict[str, bool]:
        status: dict[str, bool] = {}
        for name, spec in self.servers.items():
            if spec.transport == "stdio":
                conn = _StdioMCPConnection(name, spec)
                ok = conn.start()
                if ok:
                    self._connections[name] = conn
                status[name] = ok
            else:
                status[name] = False
        return status

    def disconnect_all(self) -> None:
        for conn in self._connections.values():
            conn.stop()
        self._connections.clear()

    def list_tools(self, server: str | None = None) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        targets = [server] if server else list(self._connections)
        for name in targets:
            conn = self._connections.get(name)
            if conn:
                out[name] = conn.tools
        return out

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any] | None = None,
                  *, mode=None) -> MCPToolResult:
        # Gating SEBELUM request dikirim: mode tanpa kapabilitas MCP tidak boleh
        # menyentuh server sama sekali.
        if mode is not None:
            from wth_ai.modes import Cap, check

            if not check(mode, Cap.MCP):
                return MCPToolResult(tool=tool, success=False, output="",
                                     error=f"mode '{mode.key}' tidak punya kapabilitas 'mcp'")
        arguments = arguments or {}
        self._audit(server, tool, arguments)
        conn = self._connections.get(server)
        if not conn:
            return MCPToolResult(tool=tool, success=False, output="", error=f"server not connected: {server}")
        result = conn.call_tool(tool, arguments)
        from wth_ai.redaction import redact_secrets

        result.output = redact_secrets(result.output)
        return result

    def _audit(self, server: str, tool: str, arguments: dict[str, Any]) -> None:
        if not self.config.mcp.policy.get("audit_log", True):
            return
        try:
            from wth_ai.redaction import redact_secrets

            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"ts": _now(), "server": server, "tool": tool,
                     "args": redact_secrets(json.dumps(arguments))}
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def format_status(self) -> str:
        lines = [
            "## MCP Client",
            f"- Enabled: {self.enabled}",
            f"- Servers configured: {', '.join(self.servers) or 'none'}",
            f"- Connected: {', '.join(self._connections) or 'none'}",
        ]
        for name, conn in self._connections.items():
            lines.append(f"- {name}: {len(conn.tools)} tools")
        return "\n".join(lines)
