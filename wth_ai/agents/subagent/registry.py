"""Subagent registry and specs."""

from __future__ import annotations

from dataclasses import dataclass

MAX_DEPTH = 2


@dataclass(frozen=True)
class SubagentSpec:
    id: str
    description: str
    skill_ids: tuple[str, ...] = ()
    vuln_classes: tuple[str, ...] = ()


SPECS: dict[str, SubagentSpec] = {
    "recon": SubagentSpec("recon", "Bounded recon", ("recon-and-methodology",)),
    "injection": SubagentSpec("injection", "Injection analysis", ("sqli-sql-injection",), ("sqli", "injection")),
    "xss": SubagentSpec("xss", "XSS analysis", ("xss-cross-site-scripting",), ("xss",)),
    "auth": SubagentSpec("auth", "Auth bypass", ("auth-sec",), ("auth", "jwt")),
    "authz": SubagentSpec("authz", "IDOR/BOLA", ("idor-broken-object-authorization",), ("idor", "authz")),
    "ssrf": SubagentSpec("ssrf", "SSRF witness", ("ssrf-server-side-request-forgery",), ("ssrf",)),
    "infra": SubagentSpec("infra", "Infra misconfig", ("unauthorized-access-common-services",)),
    "ctf": SubagentSpec("ctf", "CTF solver", ("ctf-web", "ctf-pwn", "ctf-crypto")),
    "reporter": SubagentSpec("reporter", "Report builder", ("bug-bounty-deep-testing",)),
}


def get_spec(agent_id: str) -> SubagentSpec | None:
    return SPECS.get(agent_id)
