"""MCP client manager — connect to external MCP servers via stdio/SSE."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from wth_ai.audit import audit_log
from wth_ai.config import WTHConfig
from wth_ai.mcp.client import MCPToolResult
from wth_ai.mcp.config import (MANIFEST_NAME, MCPServerSpec, load_manifest,
                               merge_manifest, parse_mcp_servers)
from wth_ai.netguard import EgressDenied, EgressGuard
from wth_ai.vault import SecretVault, is_vault_ref


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


class _HttpMCPConnection:
    """MCP JSON-RPC lewat HTTP (streamable), dengan allowlist egress.

    Host loopback selalu boleh (server MCP lokal adalah proses operator). Host
    lain harus ada di scope card: server MCP pihak ketiga adalah koneksi keluar
    dan tidak boleh mendapat pengecualian dari `EgressGuard`.
    """

    def __init__(self, name: str, spec: MCPServerSpec) -> None:
        self.name = name
        self.spec = spec
        self.url = spec.url
        self.headers = _resolve_headers(dict(spec.headers))
        self.tools: list[dict[str, Any]] = []
        self._req_id = 0
        self._lock = threading.Lock()

    def start(self) -> bool:
        host = urlsplit(self.url).hostname or ""
        if not (_scheme_allowed(self.url) and _host_allowed(host)):
            audit_log("EGRESS_DENIED", host=host, purpose=f"mcp:{self.name}")
            return False
        # Daftar tool diambil saat connect supaya `list_tools` tidak memblokir
        # turn; timeout pendek menjaga startup tetap terbatas.
        listed = self._rpc("tools/list", {}, timeout=5.0)
        self.tools = listed.get("tools", []) if isinstance(listed, dict) else []
        return True

    def stop(self) -> None:
        """HTTP tidak menyimpan koneksi; hanya paritas antarmuka."""

    def _rpc(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> Any:
        parts = urlsplit(self.url)
        if not _scheme_allowed(self.url):
            raise EgressDenied(f"mcp {self.name}: skema URL tidak diizinkan ({self.url})")
        host = parts.hostname or ""
        if not _host_allowed(host):
            raise EgressDenied(f"mcp {self.name}: host di luar allowlist ({host})")
        with self._lock:
            self._req_id += 1
            rid = self._req_id
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        # http.client, bukan urlopen: urlopen mengikuti redirect, dan MCP tidak
        # boleh melompat ke host yang tidak lolos allowlist di atas.
        conn_cls = (
            http.client.HTTPSConnection
            if parts.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_cls(host, parts.port, timeout=timeout)
        try:
            conn.request(
                "POST",
                parts.path or "/",
                body=json.dumps(payload).encode(),
                headers=headers,
            )
            response = conn.getresponse()
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeError(f"MCP HTTP gagal: {exc}") from exc
        finally:
            conn.close()
        if status >= 400:
            raise RuntimeError(f"MCP HTTP {status}")
        message = _parse_http_body(body)
        if message.get("id") != rid:
            raise RuntimeError("MCP HTTP: respons tidak cocok dengan request id")
        if "error" in message:
            raise RuntimeError(str(message["error"]))
        return message.get("result")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolResult:
        try:
            result = self._rpc(
                "tools/call", {"name": tool_name, "arguments": arguments}
            )
            content = result.get("content", []) if isinstance(result, dict) else []
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            output = "\n".join(text_parts) or json.dumps(result, default=str)
            return MCPToolResult(
                tool=tool_name, success=True, output=output[:200_000]
            )
        except Exception as exc:  # noqa: BLE001 - semua kegagalan jadi hasil tool
            return MCPToolResult(
                tool=tool_name, success=False, output="", error=str(exc)
            )


def _scheme_allowed(url: str) -> bool:
    """Hanya http/https: `file:` dan skema kustom bukan transport MCP."""
    return urlsplit(url or "").scheme in {"http", "https"}


def _host_allowed(host: str) -> bool:
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    card_path = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "scope_card.json"
    if not card_path.is_file():
        return False
    try:
        from wth_ai.scope_card import ScopeCard

        card = ScopeCard.load(card_path)
    except Exception:  # noqa: BLE001 - kartu rusak bukan izin
        return False
    guard = EgressGuard.from_patterns([*card.cidr, *card.domains])
    return guard.allows(host)


def _parse_http_body(body: str) -> dict[str, Any]:
    """Terima JSON biasa atau SSE (`data: {...}`) sesuai spesifikasi streamable."""
    text = (body or "").strip()
    if not text:
        raise RuntimeError("MCP HTTP: respons kosong")
    if text.startswith(("event:", "data:")):
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MCP HTTP: respons bukan JSON ({text[:80]})") from exc
    if not isinstance(message, dict):
        raise RuntimeError("MCP HTTP: respons bukan objek JSON")
    return message


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
        self._connections: dict[str, Any] = {}
        self._audit_path = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "logs" / "mcp_audit.jsonl"
        # P2-3 (audit 2026-11-06): state untuk rate-limit per menit, per server;
        # kuota server longgar tidak boleh dipotong oleh server lain.
        self._rates: dict[str, tuple[float, int]] = {}

    def connect_all(self) -> dict[str, bool]:
        status: dict[str, bool] = {}
        for name, spec in self.servers.items():
            if spec.transport == "stdio":
                conn: Any = _StdioMCPConnection(name, spec)
            elif spec.transport in {"http", "streamable-http"}:
                conn = _HttpMCPConnection(name, spec)
            else:
                status[name] = False
                continue
            ok = conn.start()
            if ok:
                self._connections[name] = conn
            status[name] = ok
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
        # P2-3 (audit 2026-11-06): tegakkan policy `allowed_tools` + rate-limit
        # sebelum request dikirim. Sebelum ini kedua kunci didefinisikan di
        # config tapi tidak pernah dipakai — allowlist mentah dan rate-limit
        # mati, sehingga tool berbahaya bisa dipanggil tanpa batas.
        allowed = self.config.mcp.policy.get("allowed_tools", ["*"])
        if "*" not in allowed and tool not in allowed:
            self._denied(server, tool, "allowed_tools")
            return MCPToolResult(tool=tool, success=False, output="",
                                 error=f"tool '{tool}' tidak diizinkan oleh policy allowed_tools")
        try:
            limit = int(self.config.mcp.policy.get("max_calls_per_minute", 0) or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit > 0:
            now = time.monotonic()
            window_start, count = self._rates.get(server, (now, 0))
            if now - window_start >= 60.0:
                window_start, count = now, 0
            if count >= limit:
                self._denied(server, tool, "max_calls_per_minute")
                return MCPToolResult(tool=tool, success=False, output="",
                                     error=f"batas rate-limit MCP terlampaui ({limit}/menit)")
            self._rates[server] = (window_start, count + 1)
        self._audit(server, tool, arguments)
        conn = self._connections.get(server)
        if not conn:
            return MCPToolResult(tool=tool, success=False, output="", error=f"server not connected: {server}")
        result = conn.call_tool(tool, arguments)
        from wth_ai.redaction import redact_secrets

        result.output = redact_secrets(result.output)
        return result

    def _denied(self, server: str, tool: str, reason: str) -> None:
        """Catat penolakan policy; allowlist yang diam-diam menolak sulit diaudit."""
        if not self.config.mcp.policy.get("audit_log", True):
            return
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "event": "mcp_tool_denied",
                    "server": server,
                    "tool": tool,
                    "reason": reason,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }) + "\n")
        except OSError:
            # Audit gagal tidak boleh menggagalkan panggilan tool.
            pass

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
            # Audit gagal tidak boleh menggagalkan panggilan tool.
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
