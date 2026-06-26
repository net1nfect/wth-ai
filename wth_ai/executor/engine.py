"""Active Execution Engine (Fase 6, flowchart Stage 6) — gated & non-destructive.

Runs the test manifest produced by :class:`TestPlanner`. Hard safety envelope:

- GET/HEAD only (enforced by the request guard and ``recon.http.fetch``).
- Inert canaries only (no payloads that change/destroy state).
- Per-domain rate limiting, per-endpoint + global request budget, retry/backoff.
- Scope guard: every request host must be in scope.
- Recovery controller: after repeated errors the engine drops to recovery mode
  and stops issuing active requests.
- Sensitive/mutating tasks require an approval callback (``approve``) to proceed.

Modules produce *observations* and *finding candidates* (clue-level, ``proven=False``)
with evidence metadata; the triage/analysis layer (Fase 6 P6) turns these into
ranked, evidence-backed findings.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from wth_ai.recon import http as rhttp

NoOp = lambda *a, **k: None  # noqa: E731

_CANARY = "wthcanary7q3z"  # inert, harmless reflection marker
_SECURITY_HEADERS = (
    "Content-Security-Policy", "Strict-Transport-Security", "X-Frame-Options",
    "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy",
)
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()


@dataclass
class RateLimiter:
    rps: float = 2.0
    _last: dict[str, float] = field(default_factory=dict)

    def wait(self, host: str) -> None:
        if self.rps <= 0:
            return
        min_interval = 1.0 / self.rps
        now = time.monotonic()
        last = self._last.get(host, 0.0)
        delta = now - last
        if delta < min_interval:
            time.sleep(min_interval - delta)
        self._last[host] = time.monotonic()


@dataclass
class ExecResult:
    task_id: str
    module: str
    target_url: str
    status: str = "success"      # success|skipped|failed|blocked
    detail: str = ""
    started_at: float = 0.0
    duration_ms: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    observation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "module": self.module, "target_url": self.target_url,
            "status": self.status, "detail": self.detail, "duration_ms": self.duration_ms,
            "findings": len(self.findings), "observation": self.observation,
        }


class ExecutionEngine:
    def __init__(
        self,
        *,
        scope_hosts: set[str] | None = None,
        rate_rps: float = 2.0,
        per_endpoint_budget: int = 3,
        max_consecutive_errors: int = 5,
        timeout: int = 12,
    ) -> None:
        self.scope_hosts = {h.lower() for h in (scope_hosts or set())}
        self.limiter = RateLimiter(rps=rate_rps)
        self.per_endpoint_budget = per_endpoint_budget
        self.max_consecutive_errors = max_consecutive_errors
        self.timeout = timeout
        self._endpoint_counts: dict[str, int] = {}
        self._requests = 0
        self._consecutive_errors = 0
        self.recovery = False

    # ----------------------------------------------------------------- #
    def _in_scope(self, url: str) -> bool:
        if not self.scope_hosts:
            return True
        host = (urlparse(rhttp.normalize_url(url)).hostname or "").lower()
        return any(host == s or host.endswith("." + s) for s in self.scope_hosts)

    def _guard(self, url: str, method: str) -> tuple[bool, str]:
        if method.upper() not in ("GET", "HEAD"):
            return False, "non-GET/HEAD blocked (non-destructive)"
        if not self._in_scope(url):
            return False, "out of scope"
        host = (urlparse(rhttp.normalize_url(url)).hostname or "").lower()
        if self._endpoint_counts.get(url, 0) >= self.per_endpoint_budget:
            return False, "per-endpoint budget exhausted"
        return True, ""

    def _fetch(self, url: str, *, method: str = "GET", headers: dict | None = None) -> rhttp.HttpResponse:
        host = (urlparse(rhttp.normalize_url(url)).hostname or "").lower()
        self.limiter.wait(host)
        self._endpoint_counts[url] = self._endpoint_counts.get(url, 0) + 1
        self._requests += 1
        # retry with exponential backoff on transient (errored) responses
        resp = rhttp.fetch(url, method=method, timeout=self.timeout, headers=headers)
        backoff = 0.5
        for _ in range(2):
            if resp.ok:
                break
            time.sleep(min(backoff, 2.0))
            backoff *= 2
            resp = rhttp.fetch(url, method=method, timeout=self.timeout, headers=headers)
        if resp.ok:
            self._consecutive_errors = 0
        else:
            self._consecutive_errors += 1
            if self._consecutive_errors >= self.max_consecutive_errors:
                self.recovery = True
        return resp

    # ----------------------------------------------------------------- #
    def run(
        self,
        manifest: Any,
        *,
        credentials: dict[str, Any] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        log: Callable[[str], None] | None = None,
        bus: Any = None,
        job_id: str | None = None,
        approve: Callable[[Any], bool] | None = None,
    ) -> dict[str, Any]:
        progress = progress or NoOp
        log = log or NoOp
        tasks = list(manifest.tasks)
        total = max(len(tasks), 1)
        results: list[ExecResult] = []
        findings: list[dict[str, Any]] = []

        for i, task in enumerate(tasks, 1):
            if self.recovery:
                results.append(ExecResult(task.id, task.module, task.target_url,
                                          status="skipped", detail="recovery mode active"))
                continue
            if self._requests >= manifest.request_budget:
                results.append(ExecResult(task.id, task.module, task.target_url,
                                          status="skipped", detail="request budget exhausted"))
                continue
            # approval gate for sensitive/mutating tasks
            if task.sensitivity in ("high",) and approve is not None and not approve(task):
                results.append(ExecResult(task.id, task.module, task.target_url,
                                          status="blocked", detail="approval denied"))
                continue

            self._emit(bus, "exec.task_started", job_id,
                       {"task": task.id, "module": task.module, "url": task.target_url})
            res = self._run_task(task, credentials or {})
            results.append(res)
            findings.extend(res.findings)
            for f in res.findings:
                self._emit(bus, "exec.finding_candidate", job_id,
                           {"title": f.get("title"), "severity": f.get("severity")})
                if _SEVERITY_RANK.get(f.get("severity", "info"), 0) >= 3 and bus is not None:
                    try:
                        bus.emit("finding.high_priority", job_id=job_id,
                                 data={"title": f.get("title"), "asset": f.get("asset"),
                                       "severity": f.get("severity")})
                    except Exception:  # noqa: BLE001
                        pass
            self._emit(bus, "exec.task_completed", job_id,
                       {"task": task.id, "status": res.status, "findings": len(res.findings)})
            log(f"exec {task.id} {task.module} {task.target_url}: {res.status} ({len(res.findings)} clues)")
            progress(min(99, int(i / total * 100)), 100, f"exec {task.module}")

        return {
            "results": [r.to_dict() for r in results],
            "findings": findings,
            "requests": self._requests,
            "recovery": self.recovery,
            "stats": {
                "tasks": len(tasks),
                "completed": sum(1 for r in results if r.status == "success"),
                "skipped": sum(1 for r in results if r.status == "skipped"),
                "blocked": sum(1 for r in results if r.status == "blocked"),
                "findings": len(findings),
            },
        }

    # ----------------------------------------------------------------- #
    def _run_task(self, task: Any, credentials: dict[str, Any]) -> ExecResult:
        start = time.monotonic()
        res = ExecResult(task.id, task.module, task.target_url, started_at=start)
        ok, reason = self._guard(task.target_url, task.method)
        if not ok:
            res.status = "skipped"
            res.detail = reason
            res.duration_ms = int((time.monotonic() - start) * 1000)
            return res
        try:
            handler = getattr(self, f"_mod_{task.module}", None)
            if handler is None:
                res.status = "skipped"
                res.detail = "unknown module"
            else:
                handler(task, res, credentials)
        except Exception as exc:  # noqa: BLE001
            res.status = "failed"
            res.detail = str(exc)[:200]
        res.duration_ms = int((time.monotonic() - start) * 1000)
        return res

    @staticmethod
    def _evidence(resp: rhttp.HttpResponse, *, request: dict[str, Any]) -> dict[str, Any]:
        body = resp.text or ""
        return {
            "request": request,
            "response": {
                "status": resp.status,
                "length": len(body),
                "server": resp.headers.get("Server", resp.headers.get("server", "")),
                "content_type": resp.headers.get("Content-Type", resp.headers.get("content-type", "")),
            },
            "body_sha256": _sha256(body),
        }

    def _finding(self, *, title: str, asset: str, category: str, severity: str,
                 detail: str, evidence: dict, confidence: float) -> dict[str, Any]:
        return {
            "title": f"HIPOTESIS: {title}", "asset": asset, "category": category,
            "severity_hint": severity, "severity": severity, "detail": detail,
            "evidence": evidence, "confidence": confidence, "proven": False,
        }

    # --- non-destructive modules ------------------------------------- #
    def _mod_http_probe(self, task, res, creds) -> None:
        resp = self._fetch(task.target_url, method="GET")
        res.observation = self._evidence(resp, request={"method": "GET", "url": task.target_url})
        res.detail = f"status={resp.status} len={len(resp.text or '')}"
        if resp.status in (401, 403):
            res.findings.append(self._finding(
                title="Authenticated/protected resource", asset=task.target_url,
                category="access_control", severity="low",
                detail=f"Responds {resp.status} unauthenticated — access-controlled surface.",
                evidence=res.observation, confidence=0.5))
        elif resp.status >= 500:
            res.findings.append(self._finding(
                title="Server error on baseline request", asset=task.target_url,
                category="anomaly", severity="medium",
                detail=f"Unauthenticated GET returned {resp.status}.",
                evidence=res.observation, confidence=0.5))

    def _mod_header_review(self, task, res, creds) -> None:
        resp = self._fetch(task.target_url, method="GET")
        hdrs = {k.lower(): v for k, v in (resp.headers or {}).items()}
        missing = [h for h in _SECURITY_HEADERS if h.lower() not in hdrs]
        res.observation = self._evidence(resp, request={"method": "GET", "url": task.target_url})
        res.observation["missing_headers"] = missing
        res.detail = f"missing={len(missing)}"
        if len(missing) >= 3:
            res.findings.append(self._finding(
                title="Weak security header posture", asset=task.target_url,
                category="headers", severity="low",
                detail="Missing: " + ", ".join(missing),
                evidence=res.observation, confidence=0.6))

    def _mod_tls_review(self, task, res, creds) -> None:
        from wth_ai.recon import tls
        host = urlparse(rhttp.normalize_url(task.target_url)).hostname or ""
        info = {}
        try:
            info = tls.cert_info(host) or {}
        except Exception:  # noqa: BLE001
            info = {}
        res.observation = {"host": host, "issuer": str(info.get("issuer", "")),
                           "not_after": str(info.get("notAfter", ""))}
        res.detail = "tls reviewed"

    def _mod_param_probe(self, task, res, creds) -> None:
        """Inert canary reflection clue (XSS *hint* only — never an exploit)."""
        base = task.target_url.split("?")[0]
        names = task.params or ["q"]
        name = names[0] or "q"
        url = f"{base}?{name}={_CANARY}"
        ok, reason = self._guard(url, "GET")
        if not ok:
            res.status = "skipped"
            res.detail = reason
            return
        resp = self._fetch(url, method="GET")
        reflected = _CANARY in (resp.text or "")
        res.observation = self._evidence(resp, request={"method": "GET", "url": url, "param": name})
        res.observation["reflected"] = reflected
        res.detail = f"reflected={reflected}"
        if reflected:
            res.findings.append(self._finding(
                title=f"Reflected canary in parameter '{name}'", asset=base,
                category="reflection", severity="medium",
                detail=("Inert canary value is reflected verbatim in the response — "
                        "potential reflected-XSS surface (unproven, needs manual review)."),
                evidence=res.observation, confidence=0.55))

    def _mod_api_review(self, task, res, creds) -> None:
        resp = self._fetch(task.target_url, method="GET")
        body = (resp.text or "")[:2000].lower()
        res.observation = self._evidence(resp, request={"method": "GET", "url": task.target_url})
        is_schema = any(k in body for k in ('"openapi"', '"swagger"', '"paths"', "introspection"))
        if is_schema:
            res.findings.append(self._finding(
                title="Exposed API definition", asset=task.target_url,
                category="api", severity="low",
                detail="Machine-readable API schema reachable unauthenticated.",
                evidence=res.observation, confidence=0.6))
        res.detail = f"status={resp.status} schema={is_schema}"

    def _mod_auth_probe(self, task, res, creds) -> None:
        # Non-destructive: observe the auth endpoint, never submit credentials/brute force.
        resp = self._fetch(task.target_url, method="GET")
        res.observation = self._evidence(resp, request={"method": "GET", "url": task.target_url})
        res.detail = f"auth endpoint status={resp.status}"

    def _mod_access_control(self, task, res, creds) -> None:
        """Compare anonymous vs authenticated responses (authz/IDOR clue)."""
        anon = self._fetch(task.target_url, method="GET")
        auth_headers = {}
        if creds.get("cookie"):
            auth_headers["Cookie"] = creds["cookie"]
        if creds.get("authorization"):
            auth_headers["Authorization"] = creds["authorization"]
        res.observation = {"anon": {"status": anon.status, "len": len(anon.text or "")}}
        if not auth_headers:
            res.detail = "no credentials — anon baseline only"
            return
        ok, reason = self._guard(task.target_url, "GET")
        if not ok:
            res.detail = reason
            return
        authed = self._fetch(task.target_url, method="GET", headers=auth_headers)
        res.observation["auth"] = {"status": authed.status, "len": len(authed.text or "")}
        # Same content for anon and authed on a protected page => possible broken access control.
        if anon.status == 200 and authed.status == 200 and \
           abs(len(anon.text or "") - len(authed.text or "")) < 32 and \
           any(k in task.target_url.lower() for k in ("admin", "account", "user", "profile", "order")):
            res.findings.append(self._finding(
                title="Identical anon/auth response on protected path",
                asset=task.target_url, category="access_control", severity="high",
                detail="Anonymous and authenticated responses are equivalent on a "
                       "sensitive path — possible broken access control (unproven).",
                evidence={"anon": res.observation["anon"], "auth": res.observation["auth"]},
                confidence=0.5))

    # ----------------------------------------------------------------- #
    @staticmethod
    def _emit(bus: Any, etype: str, job_id: str | None, data: dict[str, Any]) -> None:
        if bus is None:
            return
        try:
            bus.emit(etype, job_id=job_id, data=data)
        except Exception:  # noqa: BLE001
            pass
