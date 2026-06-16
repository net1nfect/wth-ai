"""Feature/signal extraction for the WTH-AI cognition layer.

Pure, deterministic, dependency-light feature engineering. Everything here is
offline-computable so the reasoning engine works without an LLM provider; the
LLM (when available) only *refines* on top of these grounded signals.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Base severity weight per vulnerability class, normalised to 0..1.
# Ordering mirrors AGENT.md testing priority:
#   auth bypass > privesc > IDOR/BOLA > SSRF > XSS > misconfig > info leak
VULN_BASE_SEVERITY: dict[str, float] = {
    "auth_bypass": 1.00,
    "auth": 0.95,
    "auth_jwt": 0.93,
    "privesc": 0.92,
    "idor": 0.88,
    "bola": 0.88,
    "ssrf": 0.82,
    "rce": 0.98,
    "sqli": 0.90,
    "race": 0.70,
    "xxe": 0.78,
    "xss": 0.60,
    "open_redirect": 0.45,
    "csrf": 0.50,
    "misconfig": 0.40,
    "info_leak": 0.30,
    "unknown": 0.35,
}

# Parameter name sensitivity (0..1). High-value names imply object references,
# auth material, or server-side fetch primitives.
HIGH_VALUE_PARAMS: frozenset[str] = frozenset({
    "id", "user_id", "uid", "uuid", "account", "account_id", "order_id",
    "file", "path", "url", "redirect", "next", "return", "callback", "dest",
    "target", "token", "key", "api_key", "email", "role", "admin", "debug",
})
MEDIUM_VALUE_PARAMS: frozenset[str] = frozenset({
    "q", "search", "query", "page", "sort", "filter", "name", "type", "lang",
})

# Tech profiles that historically amplify certain vuln classes.
PROFILE_AFFINITY: dict[str, dict[str, float]] = {
    "api": {"idor": 0.15, "bola": 0.15, "auth": 0.10, "ssrf": 0.08},
    "laravel": {"idor": 0.10, "sqli": 0.08, "auth": 0.06},
    "graphql": {"idor": 0.12, "auth": 0.10, "info_leak": 0.08},
    "wordpress": {"misconfig": 0.10, "xss": 0.06, "auth": 0.05},
    "generic_web": {},
}


def normalize_vuln_class(vuln_class: str | None) -> str:
    return (vuln_class or "unknown").strip().lower().replace("-", "_") or "unknown"


def vuln_base_severity(vuln_class: str | None) -> float:
    return VULN_BASE_SEVERITY.get(normalize_vuln_class(vuln_class), VULN_BASE_SEVERITY["unknown"])


def param_sensitivity(param: str | None) -> float:
    if not param:
        return 0.2
    p = param.lower()
    if p in HIGH_VALUE_PARAMS:
        return 1.0
    if any(k in p for k in ("id", "user", "token", "redirect", "url", "file", "path", "key")):
        return 0.7
    if p in MEDIUM_VALUE_PARAMS:
        return 0.45
    return 0.25


def endpoint_exposure(url: str | None) -> float:
    """How attack-relevant the endpoint shape is (api/admin/auth surfaces)."""
    if not url:
        return 0.3
    u = url.lower()
    score = 0.3
    if "/api/" in u or u.rstrip("/").endswith("/api"):
        score = max(score, 0.8)
    if any(seg in u for seg in ("/admin", "/internal", "/manage", "/console")):
        score = max(score, 0.9)
    if any(seg in u for seg in ("/auth", "/login", "/oauth", "/token", "/session")):
        score = max(score, 0.85)
    if any(seg in u for seg in ("/graphql", "/gql")):
        score = max(score, 0.8)
    try:
        path = urlparse(url if "://" in url else f"https://{url}").path
        depth = len([s for s in path.split("/") if s])
        score += min(0.1, depth * 0.02)
    except ValueError:
        pass
    return min(1.0, score)


def profile_affinity(tech_profile: str | None, vuln_class: str | None) -> float:
    affinities = PROFILE_AFFINITY.get((tech_profile or "generic_web").lower(), {})
    return affinities.get(normalize_vuln_class(vuln_class), 0.0)


def extract_features(hypothesis: dict, *, tech_profile: str = "generic_web") -> dict[str, float]:
    """Turn a raw hypothesis dict into normalised numeric features (0..1)."""
    vuln_class = normalize_vuln_class(hypothesis.get("vuln_class"))
    component = hypothesis.get("component") or hypothesis.get("url") or ""
    param = hypothesis.get("param", "")
    if not param:
        # Best-effort param recovery from "?name" / "param `x`" style titles.
        title = hypothesis.get("title", "")
        if "?" in component:
            param = component.split("?", 1)[1].split("=")[0]
        elif "`" in title:
            chunk = title.split("`")
            if len(chunk) >= 2:
                param = chunk[1]

    raw_priority = hypothesis.get("priority", 5)
    try:
        prior_priority = max(0.0, min(1.0, float(raw_priority) / 10.0))
    except (TypeError, ValueError):
        prior_priority = 0.5

    evidence_clarity = 1.0 if (hypothesis.get("evidence_needed") or hypothesis.get("safe_test")) else 0.4

    return {
        "base_severity": vuln_base_severity(vuln_class),
        "param_sensitivity": param_sensitivity(param),
        "endpoint_exposure": endpoint_exposure(component),
        "profile_affinity": profile_affinity(tech_profile, vuln_class),
        "prior_priority": prior_priority,
        "evidence_clarity": evidence_clarity,
    }
