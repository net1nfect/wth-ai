"""Agent terminal bridge — permission-gated shell + HTTP probes."""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from uuid import uuid4

import requests

from wth_ai.config import WTHConfig
from wth_ai.executor.runner_v2 import ExecutorRunnerV2
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.models import TerminalResult
from wth_ai.terminal.transcript import TerminalTranscriptStore
from wth_ai.tools.registry import ToolRegistry


URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)


class AgentTerminalBridge:
    """Unified terminal/HTTP access for planner, executor, and recon agents."""

    ACTOR_MIN_PERM = {
        "planner": PermissionMode.READ_ONLY_RECON,
        "executor": PermissionMode.STANDARD_TESTING,
        "recon": PermissionMode.READ_ONLY_RECON,
    }

    def __init__(
        self,
        config: WTHConfig,
        permission: PermissionManager,
        scope: ScopeManager,
        session_id: str,
        *,
        on_line: Callable[[str], None] | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self.permission = permission
        self.scope = scope
        self.transcripts = TerminalTranscriptStore(session_id)
        self.registry = registry or ToolRegistry()
        self.runner = ExecutorRunnerV2(self.registry, on_line=on_line or self._default_on_line)
        self._last_request = 0.0

    def _default_on_line(self, line: str) -> None:
        import sys
        if sys.stdout.isatty():
            print(f"  │ {line[:200]}", flush=True)

    def _throttle(self) -> None:
        rps = max(self.scope.scope.rate_limit_rps, 0.1)
        gap = 1.0 / rps
        elapsed = time.monotonic() - self._last_request
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_request = time.monotonic()

    def _new_id(self) -> str:
        return f"TX-{uuid4().hex[:10]}"

    def _urls_in_text(self, text: str) -> list[str]:
        return URL_RE.findall(text)

    def _check_scope_urls(self, text: str) -> tuple[bool, str]:
        for url in self._urls_in_text(text):
            ok, msg = self.scope.is_in_scope(url)
            if not ok:
                return False, msg
        return True, "ok"

    def run_shell(
        self,
        cmd: str,
        *,
        actor: str = "executor",
        purpose: str = "",
        approved: bool = False,
        required_mode: PermissionMode | None = None,
    ) -> TerminalResult:
        tid = self._new_id()
        min_perm = required_mode or self.ACTOR_MIN_PERM.get(actor, PermissionMode.STANDARD_TESTING)
        if not self.permission.mode_sufficient(min_perm):
            return self._blocked(tid, actor, purpose, cmd, f"Need permission {min_perm.value}, have {self.permission.mode.value}")

        scope_ok, scope_msg = self._check_scope_urls(cmd)
        if not scope_ok:
            return self._blocked(tid, actor, purpose, cmd, f"Out of scope: {scope_msg}")

        check = self.permission.check_command(cmd, required_mode=min_perm)
        if not check["allowed"]:
            return self._blocked(tid, actor, purpose, cmd, check.get("reason", "blocked"))
        if check.get("approval_needed") and not approved:
            return self._blocked(tid, actor, purpose, cmd, "Approval required — use /approve")

        self._throttle()
        stdout, stderr, code = self.runner.run_shell_live(cmd, timeout=self.config.tools.timeout_seconds)
        stdout = self.scope.redact_secrets(stdout[: self.config.tools.max_output_bytes])
        stderr = self.scope.redact_secrets(stderr[:5000])
        result = TerminalResult(
            transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
            stdout=stdout, stderr=stderr, exit_code=code,
        )
        self.transcripts.store(result)
        return result

    def http_probe(
        self,
        method: str,
        url: str,
        *,
        actor: str = "planner",
        purpose: str = "",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> TerminalResult:
        tid = self._new_id()
        min_perm = self.ACTOR_MIN_PERM.get(actor, PermissionMode.READ_ONLY_RECON)
        if not self.permission.mode_sufficient(min_perm):
            return self._blocked(tid, actor, purpose, f"{method} {url}", f"Need {min_perm.value}")

        ok, msg = self.scope.is_in_scope(url)
        if not ok:
            return self._blocked(tid, actor, purpose, f"{method} {url}", msg)

        self._throttle()
        hdrs = {"User-Agent": "WTH-AI-AgentTerminal/5.1", **(headers or {})}
        cmd = f"{method.upper()} {url}"
        try:
            r = requests.request(
                method.upper(), url, headers=hdrs, data=body, timeout=15, allow_redirects=True,
            )
            body_text = self.scope.redact_secrets(r.text[: self.config.tools.max_output_bytes])
            result = TerminalResult(
                transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
                stdout=body_text, stderr="", exit_code=0 if r.ok else 1,
                url=url, method=method.upper(), status_code=r.status_code,
            )
        except requests.RequestException as exc:
            result = TerminalResult(
                transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
                stdout="", stderr=str(exc), exit_code=1, url=url, method=method.upper(),
            )
        self.transcripts.store(result)
        return result

    def _blocked(self, tid: str, actor: str, purpose: str, cmd: str, reason: str) -> TerminalResult:
        result = TerminalResult(
            transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
            stdout="", stderr=reason, exit_code=127, blocked=True, block_reason=reason,
        )
        self.transcripts.store(result)
        return result

    def dispatch_tool(self, name: str, arguments: dict[str, Any], *, actor: str, approved: bool = False) -> TerminalResult:
        if name == "run_shell":
            return self.run_shell(
                arguments.get("command", ""),
                actor=actor,
                purpose=arguments.get("purpose", ""),
                approved=approved,
            )
        if name == "http_probe":
            return self.http_probe(
                arguments.get("method", "GET"),
                arguments.get("url", ""),
                actor=actor,
                purpose=arguments.get("purpose", ""),
                headers=arguments.get("headers"),
            )
        tid = self._new_id()
        return self._blocked(tid, actor, name, str(arguments), f"Unknown tool: {name}")
