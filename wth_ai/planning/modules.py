"""Vulnerability module taxonomy for the WTH-AI executor.

A structured catalogue of authorized web-app test modules (mirrors the executor
flowchart modules 3-12). Each module declares the vuln classes it covers, the
minimum permission required, non-destructive test families, an evidence schema,
and preconditions (e.g. IDOR needs two test accounts). This is pure data +
lookup — it never executes anything; the executor + PermissionManager remain the
only things that run commands, and only within the operator's authorized scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wth_ai.models import PermissionMode


@dataclass(frozen=True)
class TestFamily:
    """A single non-destructive technique within a module."""

    name: str
    technique: str
    destructive: bool = False
    request_cost: int = 2
    requires_auth: bool = False


@dataclass(frozen=True)
class VulnModule:
    """A web-app vulnerability test module (flowchart 3-12)."""

    id: str
    name: str
    vuln_classes: tuple[str, ...]
    permission: PermissionMode
    families: tuple[TestFamily, ...]
    evidence_proof: str
    preconditions: tuple[str, ...] = ()
    flow_ref: str = ""
    default_stop: str = "first solid evidence or budget exhausted"

    def evidence_schema(self, vuln_class: str) -> dict[str, Any]:
        return {"type": vuln_class, "proof": self.evidence_proof, "severity_min": "medium"}

    def non_destructive_families(self) -> tuple[TestFamily, ...]:
        return tuple(f for f in self.families if not f.destructive)


# Preconditions vocabulary (checked against scope by the planner).
PRECOND_TWO_ACCOUNTS = "two_accounts"
PRECOND_TEST_ACCOUNT = "test_account"


MODULES: tuple[VulnModule, ...] = (
    VulnModule(
        id="authentication",
        name="Authentication & Session",
        vuln_classes=("auth", "auth_jwt", "auth_bypass"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("login_posture", "Inspect login flow, error oracle, lockout posture", request_cost=2),
            TestFamily("session_handling", "Cookie flags, fixation clues, token reuse", request_cost=2, requires_auth=True),
            TestFamily("jwt_alg", "JWT alg:none / key-confusion probe", request_cost=2, requires_auth=True),
        ),
        evidence_proof="auth_state_change",
        preconditions=(PRECOND_TEST_ACCOUNT,),
        flow_ref="executor.3",
    ),
    VulnModule(
        id="access_control",
        name="Access Control (IDOR/BOLA/Privesc)",
        vuln_classes=("idor", "bola", "privesc"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("dual_account_diff", "Baseline user A, replay object id as user B, diff", request_cost=3, requires_auth=True),
            TestFamily("vertical_boundary", "Access admin/role-gated route as low-priv user", request_cost=2, requires_auth=True),
        ),
        evidence_proof="diff_user_data",
        preconditions=(PRECOND_TWO_ACCOUNTS,),
        flow_ref="executor.4",
    ),
    VulnModule(
        id="injection",
        name="Input Validation & Injection",
        vuln_classes=("sqli", "ssrf", "rce", "xxe", "path_traversal", "command_injection", "ldap", "nosql", "ssti"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("boolean_diff", "Boolean/time differential on injectable param", request_cost=3),
            TestFamily("oob_callback", "Inject collaborator URL, watch for OOB callback (SSRF/XXE)", request_cost=3),
            TestFamily("marker_reflection", "Inject benign marker, inspect parsing/normalisation", request_cost=2),
        ),
        evidence_proof="injection_signal",
        flow_ref="executor.5",
    ),
    VulnModule(
        id="client_side",
        name="Client-Side & Browser Security",
        vuln_classes=("xss", "dom_xss"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("reflection_marker", "Unique marker reflection + context analysis", request_cost=2),
            TestFamily("csp_posture", "CSP / mixed-content / sensitive-JS posture (read-only)", destructive=False, request_cost=1),
        ),
        evidence_proof="reflection_context",
        flow_ref="executor.6",
    ),
    VulnModule(
        id="csrf_cors",
        name="CSRF / CORS / Clickjacking",
        vuln_classes=("csrf", "cors", "clickjacking"),
        permission=PermissionMode.READ_ONLY_RECON,
        families=(
            TestFamily("cors_trust", "Probe ACAO/credentials trust with crafted Origin", request_cost=2),
            TestFamily("frame_csrf_posture", "CSRF token + SameSite + frame protection posture", request_cost=1),
        ),
        evidence_proof="policy_misconfig",
        flow_ref="executor.7",
    ),
    VulnModule(
        id="file_upload",
        name="File Upload & Processing",
        vuln_classes=("upload", "file_upload"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("type_validation", "Content-Type / extension validation posture (benign file)", request_cost=2, requires_auth=True),
        ),
        evidence_proof="upload_acceptance",
        preconditions=(PRECOND_TEST_ACCOUNT,),
        flow_ref="executor.8",
    ),
    VulnModule(
        id="api",
        name="API / GraphQL / WebSocket",
        vuln_classes=("mass_assignment", "graphql", "object_auth", "api"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("object_level_auth", "Object-level authorization checks across roles", request_cost=3, requires_auth=True),
            TestFamily("mass_assignment", "Add unexpected fields, observe state acceptance", request_cost=2, requires_auth=True),
            TestFamily("graphql_introspection", "Schema/introspection posture", request_cost=2),
        ),
        evidence_proof="object_access",
        flow_ref="executor.9",
    ),
    VulnModule(
        id="business_logic",
        name="Business Logic & Workflow",
        vuln_classes=("race", "replay", "logic", "business_logic"),
        permission=PermissionMode.STANDARD_TESTING,
        families=(
            TestFamily("replay_duplicate", "Replay/duplicate state-changing action", request_cost=2, requires_auth=True),
            TestFamily("concurrent_burst", "Concurrent burst race-condition probe", request_cost=3, requires_auth=True),
        ),
        evidence_proof="logic_violation",
        preconditions=(PRECOND_TEST_ACCOUNT,),
        flow_ref="executor.10",
    ),
    VulnModule(
        id="configuration",
        name="Configuration, Crypto, Secrets",
        vuln_classes=("misconfig", "secret", "info_leak"),
        permission=PermissionMode.READ_ONLY_RECON,
        families=(
            TestFamily("exposure_probe", "Debug/admin/backup/config exposure (read-only GET)", request_cost=1),
            TestFamily("error_leak", "Verbose error / stack leakage posture", request_cost=1),
        ),
        evidence_proof="exposure_evidence",
        flow_ref="executor.11",
    ),
    VulnModule(
        id="cloud",
        name="Cloud / Storage / Third-Party",
        vuln_classes=("storage_exposure", "bucket", "webhook", "cloud"),
        permission=PermissionMode.READ_ONLY_RECON,
        families=(
            TestFamily("bucket_posture", "Object-storage/bucket exposure posture (read-only)", request_cost=2),
            TestFamily("webhook_trust", "Webhook trust-boundary posture", request_cost=1),
        ),
        evidence_proof="exposure_evidence",
        flow_ref="executor.12",
    ),
)

MODULE_BY_ID: dict[str, VulnModule] = {m.id: m for m in MODULES}

_VULN_TO_MODULE: dict[str, VulnModule] = {}
for _m in MODULES:
    for _vc in _m.vuln_classes:
        _VULN_TO_MODULE.setdefault(_vc, _m)

# Aliases so reasoning/hypothesis vuln classes map cleanly onto modules.
_ALIASES = {
    "open_redirect": "ssrf",
    "bola": "bola",
    "auth_bypass": "auth_bypass",
    "command_injection": "command_injection",
}


def module_for_vuln_class(vuln_class: str | None) -> VulnModule | None:
    if not vuln_class:
        return None
    vc = vuln_class.strip().lower().replace("-", "_")
    if vc in _VULN_TO_MODULE:
        return _VULN_TO_MODULE[vc]
    if vc in _ALIASES:
        return _VULN_TO_MODULE.get(_ALIASES[vc])
    return None
