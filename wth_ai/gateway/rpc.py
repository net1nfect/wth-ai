"""JSON-RPC over Unix socket for gateway clients."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable

from wth_ai.workspace import wth_home


def gateway_socket_path() -> Path:
    return wth_home() / "runtime" / "gateway.sock"


def gateway_pid_path() -> Path:
    return wth_home() / "runtime" / "gateway.pid"


Handler = Callable[[str, dict[str, Any]], Any]


class GatewayRpcServer:
    """Line-delimited JSON-RPC 2.0 server."""

    def __init__(self, socket_path: Path, dispatch: Handler) -> None:
        self.socket_path = socket_path
        self.dispatch = dispatch
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass
        self._thread = threading.Thread(target=self._serve, daemon=True, name="gateway-rpc")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except OSError:
                pass

    def _serve(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            server.listen(8)
            server.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()
        finally:
            server.close()

    def _handle_conn(self, conn: socket.socket) -> None:
        with conn:
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        req = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    resp = self._process(req)
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def _process(self, req: dict[str, Any]) -> dict[str, Any]:
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        try:
            result = self.dispatch(method, params)
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": str(exc)}}
