"""Security-hardened execute_code RPC server."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from wth_ai.events import get_event_bus
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.policy.engine import denylist_match, high_risk_match
from wth_ai.scope_manager import ScopeManager
from wth_ai.workspace import wth_home

def redact_secrets(text: str) -> str:
    """WU-203: delegate to canonical `wth_ai.redaction.redact_secrets`
    (single source of truth; previously a local single-pattern copy that
    missed Bearer/AWS/JWT). Kept as a named re-export -- imported directly by
    tests and used for stdout/stderr/code_preview redaction below."""
    from wth_ai.redaction import redact_secrets as _canonical

    return _canonical(text)


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
        """Decide + (maybe) run, then emit ONE durable audit record for the
        decision -- blocked OR executed (WU-104). Splitting decision/run into
        `_decide_and_run()` keeps the audit call at a single choke point so no
        return path can escape being recorded."""
        target = str(req.get("target", ""))
        code = str(req.get("code", ""))
        lang = str(req.get("lang", "python"))
        result = self._decide_and_run(req, target, code, lang)
        self._audit(
            status=str(result.get("status", "unknown")),
            target=target, lang=lang, code=code,
            reason=str(result.get("error", "")),
        )
        return result

    def _decide_and_run(self, req: dict[str, Any], target: str, code: str, lang: str) -> dict[str, Any]:
        ok, reason = self.scope.is_in_scope(target)
        if not target or not ok:
            return {"status": "blocked", "error": reason or "target out of scope"}
        # WU-010 (NET-02): sebelumnya check_command("execute", ...) memvalidasi
        # STRING DUMMY "execute", bukan `code` yang benar-benar akan dieksekusi
        # -- konten code tidak pernah diperiksa sama sekali. mode_sufficient()
        # adalah pengecekan yang sebenarnya dimaksud (ceiling mode, tanpa
        # perlu command string palsu).
        if not self.perms.mode_sufficient(PermissionMode.STANDARD_TESTING):
            return {
                "status": "blocked",
                "error": f"Insufficient permission: need {PermissionMode.STANDARD_TESTING.value}, "
                         f"have {self.perms.mode.value}",
            }
        # WU-104: content-risk layers now via the SHARED policy-engine helpers
        # (denylist_match/high_risk_match), the exact same functions
        # evaluate_intent() uses for shell commands -- one source of truth, no
        # inline loop that could drift from DENYLIST_PATTERNS/HIGH_RISK_PATTERNS.
        code_lower = code.lower()
        denied = denylist_match(code_lower)
        if denied is not None:
            return {"status": "blocked", "error": f"Denied by global denylist in code: {denied}"}
        hr = high_risk_match(code_lower)
        if hr is not None:
            return {"status": "blocked", "error": f"High-risk pattern in code: {hr}"}
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

    def _audit(self, *, status: str, target: str, lang: str, code: str, reason: str) -> None:
        """WU-104: durable audit for the live arbitrary-code-exec surface.
        Emits to the process-wide EventBus; `AuditLogSink` (subscribed by the
        gateway daemon) persists it to SQLite. NEVER raises -- a broken or
        absent bus must never break or block execution (same isolation
        discipline as `LoopStateMachine._emit_invalid`). Full `code` is NEVER
        emitted: only a sha256 (correlation without disclosure) + a redacted,
        truncated preview, so the audit record cannot itself become a
        secret-leak sink."""
        try:
            digest = hashlib.sha256(code.encode("utf-8", "replace")).hexdigest()
            get_event_bus().emit(
                "rpc.code_execution",
                source="execute_rpc",
                data={
                    "decision": "blocked" if status == "blocked" else "executed",
                    "status": status,
                    "target": target,
                    "lang": lang,
                    "reason": reason,
                    "code_sha256": digest,
                    "code_len": len(code),
                    "code_preview": redact_secrets(code[:200]),
                },
            )
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
