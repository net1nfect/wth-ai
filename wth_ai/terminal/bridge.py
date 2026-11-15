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
# treat ordinary arguments as targets -- `config/wth_config.yaml` is
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
        # GAP-002: HTTP session persisten -- cookie jar bertahan antar probe,
        # agent cukup login sekali lalu request berikutnya membawa cookie otomatis.
        self._http_session: requests.Session | None = None

    def _http(self) -> requests.Session:
        if self._http_session is None:
            self._http_session = requests.Session()
        return self._http_session

    def _default_on_line(self, line: str) -> None:
        import sys
        if sys.stdout.isatty():
            print(f"  │ {line[:200]}", flush=True)

    def _throttle(self) -> None:
        from wth_ai.engagement.mode import is_no_limits

        if is_no_limits():
            return
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
        cleanroom: bool = False,
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
        if cleanroom:
            # GAP-007 versi tanpa-Docker (keputusan operator): isolasi via
            # throwaway cwd/HOME/TMPDIR + env dipangkas. BUKAN jail kernel —
            # di-deklarasi jujur di cleanroom.py.
            from wth_ai.executor.cleanroom import CleanRoom

            room = CleanRoom.create(label="shell")
            try:
                stdout, stderr, code = room.run(cmd, timeout=self.config.tools.timeout_seconds)
            finally:
                room.cleanup()
        else:
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
        cookies: dict[str, str] | None = None,
        proxy: str = "",
        files: dict[str, str] | None = None,
        follow_redirects: bool = True,
        max_redirects: int = 8,
        session_clear: bool = False,
    ) -> TerminalResult:
        tid = self._new_id()
        min_perm = self.ACTOR_MIN_PERM.get(actor, PermissionMode.READ_ONLY_RECON)
        if not self.permission.mode_sufficient(min_perm):
            return self._blocked(tid, actor, purpose, f"{method} {url}", f"Need {min_perm.value}")

        ok, msg = self.scope.is_in_scope(url)
        if not ok:
            return self._blocked(tid, actor, purpose, f"{method} {url}", msg)

        # WU-007 (PRD-006): http_probe sebelumnya TIDAK PUNYA approval gate
        # sama sekali (beda dari run_shell) -- LLM-driven HTTP request risk
        # tinggi (POST/PUT/DELETE/PATCH, atau konten match denylist/high-risk
        # pattern) lolos tanpa approval apa pun, bahkan secara teori.
        from wth_ai.engagement.mode import is_no_limits

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
        if is_no_limits():
            approval_needed = False
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

        # --- GAP-002/003/005: session cookie-jar persisten, proxy, multipart ---
        # Cookie jar hidup di antara probe (login sekali, probe berkali-kali).
        # Proxy dipakai apa adanya (http/https/socks5) untuk intercept Burp/mitm.
        if session_clear and self._http_session is not None:
            self._http_session.cookies.clear()
        proxies: dict[str, str] | None = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}
        upload_files: dict[str, tuple[str, Any]] = {}
        for field_name, path in (files or {}).items():
            try:
                fh = open(path, "rb")  # noqa: SIM115 - ditutup r.requests di request()
            except OSError as exc:
                return self._blocked(
                    tid, actor, purpose, f"{method} {url}", f"upload file '{path}' gagal dibuka: {exc}"
                )
            upload_files[field_name] = (os.path.basename(path), fh)

        try:
            result, _ = self._send_with_hops(
                tid, actor, purpose,
                method=method, url=url, headers=hdrs,
                body=body, cookies=cookies, proxies=proxies,
                upload_files=upload_files,
                follow_redirects=follow_redirects, max_redirects=max_redirects,
            )
        except requests.RequestException as exc:
            result = TerminalResult(
                transcript_id=tid, actor=actor, purpose=purpose, command=cmd,
                stdout="", stderr=str(exc), exit_code=1, url=url, method=method.upper(),
            )
        finally:
            for _, fh in upload_files.values():
                try:
                    fh.close()
                except OSError:
                    pass
        self.transcripts.store(result)
        return result

    def _send_with_hops(
        self,
        tid: str,
        actor: str,
        purpose: str,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
        cookies: dict[str, str] | None,
        proxies: dict[str, str] | None,
        upload_files: dict[str, tuple[str, Any]],
        follow_redirects: bool,
        max_redirects: int,
    ) -> tuple[TerminalResult, list[str]]:
        """GAP-004: manual redirect-follow dengan rekap per-hop.

        Behaviour lama (`allow_redirects=True`) hanya menyerahkan response
        FINAL; hop tengah hilang dari evidence. Ini mengejar maksimal
        `max_redirects` hop, mencatat setiap hop, dan menghormati scope:
        redirect keluar-scope dihentikan (bukan diikuti) tapi hop terakhir
        yang masih in-scope tetap dikembalikan.
        """
        from wth_ai.terminal.analysis import analyze_response
        from urllib.parse import urljoin

        chain: list[str] = []
        current_url = url
        send_method = "GET" if method == "" else method.upper()
        send_data: str | None = body
        send_files = dict(upload_files)
        final: requests.Response | None = None
        for hop in range(max_redirects + 1):
            r = self._http().request(
                send_method, current_url, headers=headers, data=send_data,
                files=send_files or None, cookies=cookies, proxies=proxies,
                timeout=15, allow_redirects=False,
            )
            final = r
            location = r.headers.get("Location", "")
            is_redirect = r.status_code in (301, 302, 303, 307, 308) and bool(location)
            if not is_redirect:
                break
            chain.append(f"#{hop + 1} {r.status_code} {current_url[:120]} -> {location[:120]}")
            if not follow_redirects:
                break
            next_url = urljoin(current_url, location)
            in_scope, scope_msg = self.scope.is_in_scope(next_url)
            if not in_scope:
                chain.append(f"#{hop + 2} STOP out-of-scope: {scope_msg}")
                break
            if r.status_code != 307 and r.status_code != 308:
                send_method = "GET" if send_method != "HEAD" else "HEAD"
                send_data = None
                send_files = {}
            current_url = next_url
        else:  # pragma: no cover - loop-exhausted: final masih redirect terakhir
            chain.append(f"#STOP max_redirects={max_redirects} tercapai")

        if final is None:  # pragma: no cover - request() selalu menghasilkan jawaban
            raise requests.RequestException("no response")
        body_text = self.scope.redact_secrets(final.text[: self.config.tools.max_output_bytes])
        # Analisa header+isi DIHITUNG SEBELUM redaksi isi supaya sidik isi tetap
        # konsisten antar-request, tapi header yang menarik juga dilaporkan
        # (routing/auth/content-type) -- inilah bahan untuk uji adaptif.
        analysis = analyze_response(
            status=final.status_code, headers=dict(final.headers), body=final.text,
            method=send_method, url=current_url,
        )
        if chain:
            analysis += "\n" + " | ".join(chain)
        stored_url = current_url if chain else url
        result = TerminalResult(
            transcript_id=tid, actor=actor, purpose=purpose,
            command=f"{method.upper()} {url}",
            stdout=body_text, stderr="", exit_code=0 if final.ok else 1,
            url=stored_url, method=send_method, status_code=final.status_code,
            response_headers={str(k): str(v) for k, v in dict(final.headers).items()},
            analysis=self.scope.redact_secrets(analysis),
            redirect_chain=chain,
        )
        return result, chain

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
            response_headers=dict(getattr(result, "response_headers", {}) or {}),
            analysis=str(getattr(result, "analysis", "") or ""),
            redirect_chain=list(getattr(result, "redirect_chain", []) or []),
        )
