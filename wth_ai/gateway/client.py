"""Gateway RPC client for thin surfaces (TUI, bots, CLI)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from wth_ai.gateway.rpc import gateway_socket_path


class GatewayClient:
    """Thin RPC client for TUI, bots, and CLI."""

    def __init__(self, session_key: str = "cli:default", socket_path: Path | None = None) -> None:
        self.session_key = session_key
        self.socket_path = socket_path or gateway_socket_path()

    def call(self, method: str, **params: Any) -> Any:
        payload = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
        raw = (json.dumps(payload) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.socket_path))
            sock.sendall(raw)
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        finally:
            sock.close()
        if not data:
            raise ConnectionError("empty gateway response")
        line = data.split(b"\n", 1)[0]
        resp = json.loads(line.decode("utf-8"))
        if "error" in resp:
            raise RuntimeError(resp["error"].get("message", "gateway error"))
        return resp.get("result")

    def ping(self) -> bool:
        try:
            return bool(self.call("ping"))
        except OSError:
            return False

    def handle_input(self, text: str) -> str:
        return str(self.call("handle_input", session_key=self.session_key, text=text))

    def get_status(self) -> dict[str, Any]:
        return dict(self.call("get_status", session_key=self.session_key))

    def gateway_status(self) -> dict[str, Any]:
        return dict(self.call("gateway_status"))


def gateway_available() -> bool:
    path = gateway_socket_path()
    return path.exists() and path.is_socket()
