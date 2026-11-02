"""Security-hardened execute_code RPC server."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.workspace import wth_home

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+"
)


def redact_secrets(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***REDACTED***", text)


class ExecuteRpcServer:
    """UDS server for sandboxed script execution."""

    def __init__(self, socket_path: Path | None = None) -> None:
        self.socket_path = socket_path or (wth_home() / "runtime" / "execute.sock")
        self.scope = ScopeManager()
        self.perms = PermissionManager()
        self._stop = threading.Event()

    def start_background(self) -> None:
        threading.Thread(target=self.serve, daemon=True, name="execute-rpc").start()

    def serve(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(4)
        server.settimeout(1.0)
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            self._handle(conn)
        server.close()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            data = conn.recv(65536)
            if not data:
                return
            try:
                req = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                conn.sendall(b'{"error":"invalid json"}')
                return
            result = self.execute(req)
            conn.sendall(json.dumps(result).encode("utf-8"))

    def execute(self, req: dict[str, Any]) -> dict[str, Any]:
        target = str(req.get("target", ""))
        code = str(req.get("code", ""))
        lang = str(req.get("lang", "python"))
        ok, reason = self.scope.is_in_scope(target)
        if not target or not ok:
            return {"status": "blocked", "error": reason or "target out of scope"}
        check = self.perms.check_command("execute", required_mode=PermissionMode.STANDARD_TESTING)
        if not check.get("allowed"):
            return {"status": "blocked", "error": check.get("reason", "execute not permitted")}
        if lang != "python":
            return {"status": "blocked", "error": f"unsupported lang: {lang}"}
        with tempfile.TemporaryDirectory(prefix="wth-exec-") as tmp:
            script = Path(tmp) / "run.py"
            script.write_text(code, encoding="utf-8")
            try:
                proc = subprocess.run(
                    ["python3", str(script)],
                    capture_output=True,
                    text=True,
                    timeout=int(req.get("timeout", 30)),
                    cwd=tmp,
                    env={"PATH": os.environ.get("PATH", ""), "HOME": tmp},
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                return {"status": "timeout", "stdout": "", "stderr": ""}
            return {
                "status": "success" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "stdout": redact_secrets(proc.stdout[:8000]),
                "stderr": redact_secrets(proc.stderr[:8000]),
            }

    def stop(self) -> None:
        self._stop.set()
