"""Vuln-class executors — IDOR, SSRF, XSS, auth, race, SQLi, SSTI, CMDi, traversal."""

from wth_ai.executor.vuln.auth import AuthVulnExecutor
from wth_ai.executor.vuln.cmdi import CmdiExecutor
from wth_ai.executor.vuln.idor import IdorExecutor
from wth_ai.executor.vuln.nuclei import NucleiExecutor
from wth_ai.executor.vuln.race import RaceExecutor
from wth_ai.executor.vuln.sqli import SqliExecutor
from wth_ai.executor.vuln.ssti import SstiExecutor
from wth_ai.executor.vuln.ssrf import SsrfExecutor
from wth_ai.executor.vuln.traversal import PathTraversalExecutor
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
    "sqli": SqliExecutor,
    "sql_injection": SqliExecutor,
    "ssti": SstiExecutor,
    "cmdi": CmdiExecutor,
    "command_injection": CmdiExecutor,
    "path_traversal": PathTraversalExecutor,
    "file_path": PathTraversalExecutor,
    "lfi": PathTraversalExecutor,
    "nuclei": NucleiExecutor,
    "scan_templates": NucleiExecutor,
}

__all__ = [
    "VULN_EXECUTORS", "IdorExecutor", "SsrfExecutor", "XssExecutor",
    "AuthVulnExecutor", "RaceExecutor", "SqliExecutor", "SstiExecutor",
    "CmdiExecutor", "PathTraversalExecutor", "NucleiExecutor",
]
