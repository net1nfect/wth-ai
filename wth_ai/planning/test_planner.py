"""Test planning layer — build a bounded, ordered execution manifest.

Reads normalized recon data and produces non-destructive test tasks, sequenced
unauthenticated-first then authenticated, capped by a request budget, and gated
by the engagement execution mode. No task is destructive: modules only perform
safe GET/HEAD probes with inert canaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from wth_ai.policy.execution_mode import capability

# module -> (needs_active, is_mutating, default_sensitivity)
EXEC_MODULES: dict[str, tuple[bool, bool, str]] = {
    "http_probe": (True, False, "safe"),       # baseline capture
    "header_review": (True, False, "safe"),    # security header posture
    "tls_review": (True, False, "safe"),       # certificate posture
    "param_probe": (True, False, "medium"),    # inert canary reflection clue
    "access_control": (True, False, "medium"), # anon vs authenticated compare
    "auth_probe": (True, False, "medium"),     # auth endpoint behavior (no brute force)
    "api_review": (True, False, "medium"),     # API/schema endpoint review
}


@dataclass
class TestTask:
    id: str
    module: str
    target_url: str
    method: str = "GET"
    authenticated: bool = False
    params: list[str] = field(default_factory=list)
    sensitivity: str = "safe"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "module": self.module, "target_url": self.target_url,
            "method": self.method, "authenticated": self.authenticated,
            "params": self.params, "sensitivity": self.sensitivity, "reason": self.reason,
        }


@dataclass
class ExecutionManifest:
    target: str
    mode: str = "safe-active"
    request_budget: int = 200
    tasks: list[TestTask] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    stop_conditions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target, "mode": self.mode, "request_budget": self.request_budget,
            "task_count": len(self.tasks), "tasks": [t.to_dict() for t in self.tasks],
            "skipped": self.skipped, "stop_conditions": self.stop_conditions,
        }


class TestPlanner:
    def build(
        self,
        target: str,
        normalized: dict[str, Any],
        *,
        mode: str = "safe-active",
        request_budget: int = 200,
        has_credentials: bool = False,
        max_tasks: int = 60,
    ) -> ExecutionManifest:
        cap = capability(mode)
        man = ExecutionManifest(
            target=target, mode=mode, request_budget=request_budget,
            stop_conditions={
                "max_requests": request_budget,
                "max_consecutive_errors": 5,
                "max_findings": 100,
            },
        )
        if not cap.allow_active:
            man.skipped.append({"reason": f"mode '{mode}' is non-active; no test tasks planned"})
            return man

        probes = normalized.get("http_probes", []) or []
        endpoints = normalized.get("endpoints", []) or []
        params = normalized.get("params", []) or []
        headers = normalized.get("headers", []) or []

        alive = [p["url"] for p in probes if p.get("url")]
        # de-dup ordered alive URLs, plus endpoints
        seen: set[str] = set()
        targets_unauth: list[tuple[str, str, str]] = []  # (module, url, reason)

        def add(module: str, url: str, reason: str) -> None:
            key = f"{module}|{url}"
            if not url or key in seen:
                return
            seen.add(key)
            targets_unauth.append((module, url, reason))

        for url in alive[:max_tasks]:
            add("http_probe", url, "alive baseline")
        for hp in headers[:max_tasks]:
            add("header_review", hp.get("url", ""), "security header posture")
        if alive:
            add("tls_review", alive[0], "certificate posture")

        # parameter reflection (inert canary) on URLs that carry params
        param_urls: dict[str, list[str]] = {}
        for p in params:
            u = (p.get("url") or "").split("?")[0]
            if u:
                param_urls.setdefault(u, []).append(p.get("name", ""))
        for u, names in list(param_urls.items())[:max_tasks]:
            add("param_probe", u, f"reflection clue for {len(names)} param(s)")

        # endpoints by kind -> api/auth modules
        for ep in endpoints:
            url = ep.get("url", "")
            kind = ep.get("kind", "web")
            if kind in ("api", "api_schema", "graphql"):
                add("api_review", url, f"api endpoint ({kind})")
            elif kind == "auth":
                add("auth_probe", url, "authentication surface")

        # Build tasks (unauthenticated first)
        tid = 0
        for module, url, reason in targets_unauth:
            needs_active, mutating, sens = EXEC_MODULES.get(module, (True, False, "safe"))
            if mutating and not cap.allow_mutation:
                man.skipped.append({"task": f"{module} {url}", "reason": "mutation not allowed in mode"})
                continue
            tid += 1
            man.tasks.append(TestTask(
                id=f"T{tid:03d}", module=module, target_url=url,
                params=param_urls.get(url.split("?")[0], []) if module == "param_probe" else [],
                sensitivity=sens, reason=reason, authenticated=False,
            ))

        # Authenticated pass (role comparison) only when creds + mode permit
        if has_credentials and cap.allow_authenticated:
            for module, url, reason in targets_unauth:
                if module not in ("http_probe", "access_control", "api_review", "auth_probe"):
                    continue
                tid += 1
                man.tasks.append(TestTask(
                    id=f"T{tid:03d}", module="access_control", target_url=url,
                    sensitivity="medium", reason=f"authenticated compare: {reason}",
                    authenticated=True,
                ))
        elif not has_credentials:
            man.skipped.append({"reason": "no test credentials -> authenticated/access-control pass skipped"})

        # Enforce task cap
        if len(man.tasks) > max_tasks:
            for t in man.tasks[max_tasks:]:
                man.skipped.append({"task": f"{t.module} {t.target_url}", "reason": "task cap reached"})
            man.tasks = man.tasks[:max_tasks]
        return man
