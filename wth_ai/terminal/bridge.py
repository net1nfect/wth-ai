"""Agent terminal bridge — permission-gated shell + HTTP probes."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Callable
from uuid import uuid4

import requests

from wth_ai.config import WTHConfig
from wth_ai.executor.runner_v2 import ExecutorRunnerV2
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.policy.engine import denylist_match, high_risk_match
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.models import TerminalResult
from wth_ai.terminal.transcript import TerminalTranscriptStore
from wth_ai.tools.registry import ToolRegistry
from wth_ai.vault import SecretVault, is_vault_ref


URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)

# Bare hostname / host:port forms -- `nmap -sV host.tld`, `dig sub.host.tld`.
_BARE_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$", re.I
)

# argv[0] values whose operands can be a bare host, so the scope check has to
# look past the URL-only scan: `nmap -sV other.tld` carries no scheme, so the
# old `_check_scope_urls()` (http/https only) waved it through while
# `curl http://other.tld/x` was correctly blocked.
#
# Deliberately narrow. Scanning EVERY command for hostname-shaped tokens would
# treat ordinary arguments as targets -- `config/wth_ai_config.yaml` is
# hostname-shaped (`wth_config` + `yaml`) -- and start blocking normal work.
_NETWORK_TOOLS = frozenset({
    "nmap", "masscan", "naabu", "rustscan", "zmap", "unicornscan", "hping3",
    "nrich", "sx", "nikto", "nuclei", "sqlmap", "ffuf", "gobuster",
    "feroxbuster", "dirsearch", "dirb", "wfuzz", "dnsx", "puredns",
    "shuffledns", "massdns", "zdns", "fierce", "dnsrecon", "dnsenum",
    "dig", "host", "nslookup", "whois", "whatweb", "wafw00f", "tlsx",
    "testssl", "sslscan", "sslyze", "subfinder", "amass", "assetfinder",
    "katana", "gospider", "hakrawler", "nmap.exe",
})

# WU-007 (PRD-006): HTTP method risk classification untuk http_probe approval
# gate. GET/HEAD/OPTIONS dianggap idempotent/read-only (HTTP semantics
# standar) -- tidak butuh approval. Method mutating SELALU butuh approval,
# terlepas dari isi URL/body (beda dari run_shell yang risknya digerakkan
# tool-resolution -- HTTP tidak punya konsep "tool" argv[0] yang setara).
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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

    def _bare_hosts_in_command(self, text: str) -> list[str]:
        """Hostname operands of a network tool that carries no URL scheme."""
        from wth_ai.shell_runner import tokenize_command

        argv = tokenize_command(text)
        if not argv:
            return []
        if os.path.basename(argv[0]).lower() not in _NETWORK_TOOLS:
            return []
        hosts: list[str] = []
        for token in argv[1:]:
            if token.startswith("-"):
                continue
            value = token.split("=", 1)[-1] if "=" in token else token
            value = value.split("/", 1)[0].split(":", 1)[0].strip()
            if value and _BARE_HOST_RE.match(value):
                hosts.append(value)
        return hosts

    def _check_scope_urls(self, text: str) -> tuple[bool, str]:
        for url in self._urls_in_text(text):
            ok, msg = self.scope.is_in_scope(url)
            if not ok:
                return False, msg
        for host in self._bare_hosts_in_command(text):
            ok, msg = self.scope.is_in_scope(host)
            if not ok:
                return False, f"host {host} — {msg}"
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

        # P0 exit-review fix (HIGH): `approved` sekarang benar-benar dioper ke
        # check_command()/evaluate_intent() -- sebelumnya check_command() TIDAK
        # PUNYA parameter approved sama sekali, jadi REQUIRE_HUMAN/high-risk
        # SELALU allowed=False apa pun approved-nya, dan blok kedua di bawah
        # ini (approval_needed and not approved) tidak pernah tercapai.
        check = self.permission.check_command(cmd, required_mode=min_perm, approved=approved)
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

    def _resolve_vault_headers(self, headers: dict[str, str]) -> tuple[dict[str, str] | None, str]:
        """WU-107 (PRD-011/ADR-008, target credential injection): resolve
        `vault:<name>` reference VALUES in outgoing request headers
        just-in-time, so a caller (an LLM tool-call included) can
        authenticate a target request via e.g.
        `{"Authorization": "vault:target_api_token"}` without ever
        generating or seeing the plaintext secret. Header NAMES are never
        touched, only values matching the vault: reference prefix -- plain
        literal header values pass through unchanged (the common case, zero
        extra vault decrypt cost via the short-circuit below). Resolved
        plaintext is used ONLY for the outgoing `requests.request()` call in
        `http_probe()` -- never stored back into `TerminalResult`/
        transcripts (headers were never part of the stored transcript to
        begin with, only method+url+response body).

        Fail-closed: an explicitly-referenced vault:<name> that can't be
        resolved (missing entry, locked/corrupt vault) returns `(None, msg)`
        -- callers must BLOCK the request rather than send a broken/leaky
        literal "vault:<name>" or "None" string as the actual header value.

        Scope decision (honest slice, not the full target design): resolves
        ANY vault:<name> ref present in `headers`, unlike the target
        architecture's `Capability.allow_credential_injection` gate
        (DATA-MODEL.md) which would restrict WHICH actor/capability may
        inject WHICH ref via `Engagement.credentials_ref` -- that gate needs
        `EngagementStore` broker-fetch integration (WU-101's deferred
        remainder) and per-actor Capability resolution (WU-007/WU-011
        remainder) to mean anything real; building it now against an
        `Engagement.credentials_ref` list nothing populates would be hollow
        architecture (same discipline as WU-102's own scope decision).
        `body` (vs `headers`) and `run_shell()`'s inline command-string refs
        are a different, harder substitution shape (risk of partial-string
        collisions) -- deliberately out of scope here, left as a documented
        follow-up rather than silently expanded into.
        """
        if not any(is_vault_ref(v) for v in headers.values()):
            return headers, ""
        vault = SecretVault()
        resolved: dict[str, str] = {}
        for key, value in headers.items():
            if is_vault_ref(value):
                plain = vault.resolve(value)
                if plain is None:
                    return None, f"Cannot resolve {value} (header {key!r}) — check vault entry exists"
                resolved[key] = plain
            else:
                resolved[key] = value
        return resolved, ""

    def http_probe(
        self,
        method: str,
        url: str,
        *,
        actor: str = "planner",
        purpose: str = "",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        approved: bool = False,
    ) -> TerminalResult:
        tid = self._new_id()
        min_perm = self.ACTOR_MIN_PERM.get(actor, PermissionMode.READ_ONLY_RECON)
        if not self.permission.mode_sufficient(min_perm):
            return self._blocked(tid, actor, purpose, f"{method} {url}", f"Need {min_perm.value}")

        ok, msg = self.scope.is_in_scope(url)
        if not ok:
            return self._blocked(tid, actor, purpose, f"{method} {url}", msg)

        # Lapis kedua: allowlist egress. ScopeManager menjawab "apakah target ini
        # dalam izin"; guard ini menjawab "apakah koneksi keluar ini boleh
        # terjadi sama sekali" — dan mencatat EGRESS_DENIED ke audit log.
        from urllib.parse import urlparse

        from wth_ai.netguard import EgressDenied, EgressGuard

        guard = EgressGuard.from_patterns(self.scope.scope.in_scope)
        try:
            guard.check(urlparse(url).hostname or "")
        except EgressDenied as exc:
            return self._blocked(tid, actor, purpose, f"{method} {url}", str(exc))

        # WU-007 (PRD-006): http_probe sebelumnya TIDAK PUNYA approval gate
        # sama sekali (beda dari run_shell) -- LLM-driven HTTP request risk
        # tinggi (POST/PUT/DELETE/PATCH, atau konten match denylist/high-risk
        # pattern) lolos tanpa approval apa pun, bahkan secara teori.
        method_upper = method.upper()
        content_lower = f"{method_upper} {url} {body or ''}".lower()
        # WU-104: shared content-risk helpers (same source of truth as
        # evaluate_intent()/ExecuteRpcServer) -- was an inline duplicate loop.
        denied = denylist_match(content_lower)
        if denied is not None:
            return self._blocked(tid, actor, purpose, f"{method} {url}", f"Denied by global denylist: {denied}")
        approval_needed = method_upper not in SAFE_HTTP_METHODS
        if not approval_needed and high_risk_match(content_lower) is not None:
            approval_needed = True
        # Opt-in destruktif per engagement: kalau operator sudah menyatakan
        # (scope.destructive_allowed), method yang mengubah data boleh jalan TANPA
        # gate approval per-request. Default tetap butuh approval -- alat ini tidak
        # boleh destruktif karena kebetulan.
        destructive = bool(getattr(self.scope, "destructive_allowed", False))
        if approval_needed and destructive and not approved:
            self._audit_destructive(actor, purpose, f"{method_upper} {url}")
            approval_needed = False
        if approval_needed and not approved:
            return self._blocked(tid, actor, purpose, f"{method} {url}", "Approval required — use /approve")

        self._throttle()
        hdrs = {"User-Agent": "WTH-AI-AgentTerminal/5.1", **(headers or {})}
        hdrs, vault_err = self._resolve_vault_headers(hdrs)
        if hdrs is None:
            return self._blocked(tid, actor, purpose, f"{method} {url}", vault_err)
        cmd = f"{method.upper()} {url}"
        try:
            r = requests.request(
                method.upper(), url, headers=hdrs, data=body, timeout=15, allow_redirects=True,
            )
            body_text = self.scope.redact_secrets(r.text[: self.config.tools.max_output_bytes])
            from wth_ai.terminal.analysis import analyze_response

            # Analisa header+isi DIHITUNG SEBELUM redaksi isi supaya sidik isi tetap
            # konsisten antar-request, tapi header yang menarik juga dilaporkan
            # (routing/auth/content-type) -- inilah bahan untuk uji adaptif.
            analysis = analyze_response(
                status=r.status_code, headers=dict(r.headers), body=r.text,
                method=method.upper(), url=url,
            )
            result = TerminalResult(
                transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
                stdout=body_text, stderr="", exit_code=0 if r.ok else 1,
                url=url, method=method.upper(), status_code=r.status_code,
                analysis=self.scope.redact_secrets(analysis),
            )
        except requests.RequestException as exc:
            result = TerminalResult(
                transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
                stdout="", stderr=str(exc), exit_code=1, url=url, method=method.upper(),
            )
        self.transcripts.store(result)
        return result

    def _audit_destructive(self, actor: str, purpose: str, cmd: str) -> None:
        """Catat aksi destruktif yang diizinkan flag engagement.

        Destruktif boleh, TAPI tidak boleh tak terlihat: nama aktor, tujuan, dan
        perintahnya masuk audit log supaya bisa ditelusuri setelahnya.
        """
        try:
            from wth_ai.audit import audit_log

            audit_log(
                "destructive_action_allowed",
                actor=actor,
                purpose=purpose,
                command=cmd[:200],
            )
        except Exception:  # noqa: BLE001 - audit tidak boleh menggagalkan aksi
            pass

    def _blocked(self, tid: str, actor: str, purpose: str, cmd: str, reason: str) -> TerminalResult:
        result = TerminalResult(
            transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
            stdout="", stderr=reason, exit_code=127, blocked=True, block_reason=reason,
        )
        self.transcripts.store(result)
        return result

    def dispatch_tool(self, name: str, arguments: dict[str, Any], *, actor: str, approved: bool = False) -> TerminalResult:
        """Facade over `ExecutionBroker` (WU-102, PRD-011) -- call site
        signature/return-shape unchanged (`ActionIntent`/`ExecutionResult`
        translation is lossless, see `broker/models.py`)."""
        from wth_ai.broker.broker import ExecutionBroker
        from wth_ai.broker.models import ActionIntent

        intent = ActionIntent(
            intent_id=f"AI-{uuid4().hex[:10]}",
            tool_name=name,
            arguments=arguments,
            actor=actor,
            approved=approved,
            purpose=arguments.get("purpose", ""),
        )
        result = ExecutionBroker(self).execute(intent)
        return TerminalResult(
            transcript_id=result.transcript_id,
            actor=result.actor,
            purpose=result.purpose,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            blocked=result.blocked,
            block_reason=result.block_reason,
            url=result.url,
            method=result.method,
            status_code=result.status_code,
        )
