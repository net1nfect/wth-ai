"""Vuln-class executors — IDOR, SSRF, XSS, auth, race."""

from wth_ai.executor.vuln.auth import AuthVulnExecutor
from wth_ai.executor.vuln.idor import IdorExecutor
from wth_ai.executor.vuln.race import RaceExecutor
from wth_ai.executor.vuln.ssrf import SsrfExecutor
from wth_ai.executor.vuln.xss import XssExecutor

VULN_EXECUTORS: dict[str, type] = {
    "idor": IdorExecutor,
    "bola": IdorExecutor,
    "ssrf": SsrfExecutor,
    "xss": XssExecutor,
    "auth": AuthVulnExecutor,
    "auth_jwt": AuthVulnExecutor,
    "jwt": AuthVulnExecutor,
    "race": RaceExecutor,
}

__all__ = [
    "VULN_EXECUTORS", "IdorExecutor", "SsrfExecutor", "XssExecutor",
    "AuthVulnExecutor", "RaceExecutor",
]
