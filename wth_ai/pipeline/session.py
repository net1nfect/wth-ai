"""Satu-satunya transport untuk pipeline probe.

Executor lama memanggil `requests.get()` sendiri-sendiri; baseline dan probe
tersebar di dalam loop masing-masing. Di sini baseline disimpan sekali, setiap
probe menghasilkan `ProbeOutcome`, dan diff dihitung deterministik.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Iterable

import requests

from wth_ai.state.normalizer import (
    Observation,
    diff_observations,
    observation_from_result,
    salient_headers,
)
from wth_ai.pipeline.models import ProbeOutcome, ProbePlan, RequestSpec, Surface

Sender = Callable[[RequestSpec], Any]


def scope_guard_from(scope: Any) -> Any:
    """Build a scope guard from a `Scope` dataclass (or a ScopeManager).

    Pipeline call sites hold `state.scope` (a `Scope`), while the canonical
    policy lives in `ScopeManager`. Rather than re-implementing the matching
    rules here (which would drift), reuse `ScopeManager` and seed it with the
    exact scope object. Returns `None` when there is no scope, so legacy
    callers keep working.
    """
    if scope is None:
        return None
    if hasattr(scope, "is_in_scope"):
        return scope  # already a manager-like guard
    # No scope POLICY configured => nothing to enforce (isolated component use,
    # e.g. a single vuln executor unit-tested without an engagement). The
    # runtime path always has target/in_scope/authorized set, where the guard
    # IS active and fail-closed. Treating "no engagement" as an allow is only
    # safe because `ExecutorAgent.execute_module()` refuses to run any vuln
    # module without a scope at all -- this helper never widens that gate.
    if not (getattr(scope, "target", "") or getattr(scope, "in_scope", None)
            or getattr(scope, "authorized", False)):
        return None
    try:
        from wth_ai.scope_manager import ScopeManager

        mgr = ScopeManager()
        mgr.scope = scope
        return mgr
    except Exception:  # noqa: BLE001 - never break the request path on import issues
        return None


class ProbeSession:
    """Baseline + probe untuk satu endpoint. Tidak memilih payload."""

    def __init__(
        self,
        *,
        sender: Sender | None = None,
        timeout: float = 8.0,
        rate_limit_rps: float = 0.0,
        user_agent: str = "WTH-AI-Pipeline/1.0",
        scope_guard: Any = None,
    ) -> None:
        self.sender = sender
        self.timeout = timeout
        self.rate_limit_rps = max(0.0, float(rate_limit_rps or 0.0))
        # Enforcement di TITIK EKSEKUSI (prioritas #4): `execute_module()` sudah
        # mengecek scope sebelum memilih executor, tapi ProbeSession adalah
        # satu-satunya transport HTTP pipeline dan bisa dipanggil langsung oleh
        # dispatch/runner lain. Dengan guard di sini, policy tidak hanya hidup
        # di pemanggil -- request out-of-scope tidak pernah membuka koneksi.
        # `scope_guard` adalah objek apa pun dengan `is_in_scope(url) -> (bool, str)`.
        # `None` = perilaku lama (backward-compatible untuk unit test pipeline).
        self.scope_guard = scope_guard
        self.blocked_count = 0
        self._http = requests.Session()
        self._http.headers.update({"User-Agent": user_agent})
        self._last_request = 0.0

    def _scope_decision(self, url: str) -> tuple[bool, str]:
        """Fail-closed scope check. No guard => allow (legacy); guard present
        but URL empty/unparseable => deny, never silently allow."""
        if self.scope_guard is None:
            return True, "no scope guard"
        if not url or not str(url).strip():
            return False, "empty URL cannot be scope-checked"
        try:
            allowed, reason = self.scope_guard.is_in_scope(str(url))
        except Exception as exc:  # noqa: BLE001 - guard failure must deny
            return False, f"scope guard error (fail-closed): {exc}"
        return bool(allowed), str(reason or "")

    def _blocked_result(self, request: RequestSpec, reason: str) -> dict[str, Any]:
        self.blocked_count += 1
        body = f"BLOCKED_BY_SCOPE: {reason}"
        # Stable transcript id derived from the decision, not a random UUID:
        # the same blocked request produces the same evidence id, so repeated
        # blocked probes dedupe in evidence/audit instead of piling up.
        digest = hashlib.sha256(f"{request.method}:{request.url}:{reason}".encode()).hexdigest()[:12]
        return {
            "method": request.method,
            "url": request.url,
            "status_code": 0,
            "stdout": body,
            "response_headers": {},
            "elapsed_ms": 0.0,
            "command": f"{request.method.upper()} {request.url}",
            "blocked": True,
            "block_reason": reason,
            "transcript_id": f"TX-BLOCK-{digest}",
        }

    def _throttle(self) -> None:
        if self.rate_limit_rps <= 0:
            return
        interval = 1.0 / self.rate_limit_rps
        wait = interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _send_requests(self, request: RequestSpec) -> Any:
        # Defense-in-depth: direct callers of this private helper still get the
        # guard, but `_observe` is the single accounting point for blocked_count.
        allowed, reason = self._scope_decision(request.url)
        if not allowed:
            return self._blocked_result(request, reason)
        self._throttle()
        started = time.perf_counter()
        try:
            method = request.method.upper()
            # Module-level helpers (bukan Session.request) supaya monkeypatch
            # `requests.get` di test scope/credential tetap menjadi bukti
            # reachability. Session tetap dipakai untuk method lain (cookie
            # jar dibutuhkan oleh alur multi-step).
            if method in {"GET", "HEAD"}:
                response = requests.get(
                    request.url,
                    headers=request.headers or None,
                    data=request.body or None,
                    timeout=self.timeout,
                    allow_redirects=bool(request.follow_redirects),
                )
            elif method == "POST":
                response = requests.post(
                    request.url,
                    headers=request.headers or None,
                    data=request.body or None,
                    timeout=self.timeout,
                    allow_redirects=bool(request.follow_redirects),
                )
            else:
                response = self._http.request(
                    method,
                    request.url,
                    headers=request.headers or None,
                    data=request.body or None,
                    timeout=self.timeout,
                    allow_redirects=bool(request.follow_redirects),
                )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            body = getattr(response, "text", "") or ""
            return {
                "method": request.method,
                "url": str(getattr(response, "url", "") or request.url),
                "status_code": getattr(response, "status_code", 0) or 0,
                "stdout": body,
                "response_headers": dict(getattr(response, "headers", {}) or {}),
                "elapsed_ms": elapsed_ms,
                "command": f"{request.method.upper()} {request.url}",
            }
        except requests.RequestException as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return {
                "method": request.method,
                "url": request.url,
                "status_code": 0,
                "stdout": f"ERR:{exc}",
                "response_headers": {},
                "elapsed_ms": elapsed_ms,
                "command": f"{request.method.upper()} {request.url}",
            }

    def _observe(self, request: RequestSpec, *, role: str) -> tuple[Observation, str]:
        # One enforcement point for BOTH transports (injected sender and the
        # built-in requests path). A blocked request returns a synthetic result
        # shaped like a normal one, so evidence/verdict still records the
        # decision instead of losing it.
        allowed, reason = self._scope_decision(request.url)
        if not allowed:
            result: Any = self._blocked_result(request, reason)
        else:
            result = self.sender(request) if self.sender is not None else self._send_requests(request)
        self._last_request = time.monotonic()
        body = str(getattr(result, "stdout", "") or (result.get("stdout") if isinstance(result, dict) else "") or "")
        headers = getattr(result, "response_headers", None)
        if headers is None and isinstance(result, dict):
            headers = result.get("response_headers") or {}
        observation = observation_from_result(result, role=role, source="pipeline")
        if not observation.headers and headers:
            observation = Observation(
                obs_id=observation.obs_id,
                method=observation.method,
                url=observation.url,
                status=observation.status,
                body_length=observation.body_length,
                body_sha=observation.body_sha,
                headers=salient_headers(headers),
                elapsed_ms=observation.elapsed_ms,
                role=role,
                transcript_id=observation.transcript_id,
                source="pipeline",
                snippet=observation.snippet,
            )
        return observation, body

    def baseline(self, surface: Surface) -> tuple[Observation, str]:
        return self._observe(surface.original(), role="baseline")

    def send(self, request: RequestSpec, *, role: str = "probe") -> tuple[Observation, str]:
        """Kirim RequestSpec eksplisit (dipakai teknik yang butuh request referensi)."""
        return self._observe(request, role=role)

    def probe(self, plan: ProbePlan, baseline: Observation) -> ProbeOutcome:
        observation, body = self._observe(plan.request, role=plan.role)
        return ProbeOutcome(
            plan=plan,
            observation=observation,
            body=body,
            diff=diff_observations(baseline, observation),
        )

    def run(
        self,
        surface: Surface,
        plans: Iterable[ProbePlan],
    ) -> tuple[Observation, list[ProbeOutcome]]:
        baseline, _base_body = self.baseline(surface)
        outcomes = [self.probe(plan, baseline) for plan in plans]
        return baseline, outcomes

    def burst(self, plans: Iterable[ProbePlan], baseline: Observation) -> list[ProbeOutcome]:
        """Kirim rencana secara paralel — hanya untuk kelas yang memang butuh race.

        `probe()` tetap jalur normal. Burst tidak dipakai teknik generik supaya
        efek samping/urutan state tidak berubah.
        """
        from concurrent.futures import ThreadPoolExecutor

        items = list(plans)
        if not items:
            return []
        workers = min(20, len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda plan: self.probe(plan, baseline), items))
