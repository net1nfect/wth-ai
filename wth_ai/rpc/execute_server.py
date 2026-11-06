"""Security-hardened execute_code RPC server."""

from __future__ import annotations

import json
import os
import shutil
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

from wth_ai.redaction import redact_secrets as _canonical_redact


def redact_secrets(text: str) -> str:
    """P1-0: satu redaktor; nama lama dipertahankan untuk pemanggil/test."""
    return _canonical_redact(text)


def _parse_timeout(raw: object, *, default: int = 30, maximum: int = 300) -> int:
    """Timeout request RPC: angka positif, dipotong ke `maximum`.

    Sebelum ini `int(req.get("timeout", 30))` menerima string arbitrer dan
    angka negatif; yang pertama melaporkan traceback, yang kedua langsung
    `TimeoutExpired` sebelum kode sempat berjalan.
    """
    if raw is None:
        return default
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise ValueError(f"timeout tidak valid: {raw!r}") from None
    if value <= 0:
        raise ValueError(f"timeout harus > 0: {value}")
    return min(value, maximum)


class ExecuteRpcServer:
    """UDS server for sandboxed script execution."""

    def __init__(self, socket_path: Path | None = None) -> None:
        self.socket_path = socket_path or (wth_home() / "runtime" / "execute.sock")
        self.scope = ScopeManager()
        self.perms = PermissionManager()
        self._stop = threading.Event()

    def _sandbox_argv(self, script_dir: Path) -> list[str]:
        """Argv docker: mount script read-only, tanpa jaringan keluar.

        P2-14: sebelumnya kode RPC berjalan sebagai proses host dengan akses
        filesystem dan jaringan penuh, jadi `execute_code` setara shell lokal.
        """
        from wth_ai.containment import SandboxSpec

        spec = SandboxSpec(extra_mounts=(f"{script_dir}:/work:ro",))
        return spec.docker_args(["python3", "/work/run.py"])

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

        # P1-2 (audit 2026-11-06): scan payload kode terhadap denylist global /
        # high-risk, sama seperti surface terminal/bridge. Sebelum ini `execute()`
        # hanya cek permission (`execute`, tool generik) — kode destruktif
        # (`rm -rf /`, `mkfs`, `dd`) tereksekusi bila lolos gate permission,
        # bahkan di UNRESTRICTED yang seharusnya tetap menerapkan deny global.
        from wth_ai.policy.engine import denylist_match, high_risk_match

        code_lower = code.lower()
        denied = denylist_match(code_lower)
        if denied is not None:
            return {"status": "blocked",
                    "error": f"Denied by global denylist: {denied}"}
        risky = high_risk_match(code_lower)
        if risky is not None:
            return {"status": "blocked",
                    "error": f"Denied by high-risk pattern: {risky}"}

        try:
            timeout_s = _parse_timeout(req.get("timeout"))
        except ValueError as exc:
            return {"status": "blocked", "error": str(exc)}

        with tempfile.TemporaryDirectory(prefix="wth-exec-") as tmp:
            script = Path(tmp) / "run.py"
            script.write_text(code, encoding="utf-8")
            if shutil.which("docker") is None:
                return {"status": "blocked",
                        "error": "sandbox tidak tersedia (docker)"}
            try:
                proc = subprocess.run(
                    self._sandbox_argv(Path(tmp)),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
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
