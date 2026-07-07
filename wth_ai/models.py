"""Shared data models for WTH-AI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

try:
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = object  # type: ignore
    Field = lambda *a, **k: None  # type: ignore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tech_stack_blob(tech_stack: str | dict[str, Any] | None) -> str:
    """Flatten tech stack metadata to a lowercase search blob."""
    if isinstance(tech_stack, str):
        return tech_stack.strip().lower()
    if not isinstance(tech_stack, dict) or not tech_stack:
        return ""
    parts: list[str] = []
    for key, value in tech_stack.items():
        if isinstance(value, dict):
            parts.extend(f"{sub_key}:{sub_val}" for sub_key, sub_val in value.items())
        elif isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value)
        else:
            parts.append(f"{key}:{value}")
    return " ".join(parts).strip().lower()


def tech_stack_key(
    tech_stack: str | dict[str, Any] | None,
    *,
    fallback: str = "generic_web",
) -> str:
    """Normalize tech stack metadata to a hunt-memory lookup key."""
    if isinstance(tech_stack, str):
        key = tech_stack.strip().lower()
        return key or fallback
    blob = tech_stack_blob(tech_stack)
    if not blob:
        return fallback
    for token in (
        "laravel", "django", "rails", "wordpress", "graphql",
        "spring", "php", "java", "node", "nginx", "apache",
    ):
        if token in blob:
            return token
    if "api" in blob:
        return "api"
    return blob.split()[0][:64] or fallback


class PermissionMode(str, Enum):
    READ_ONLY_RECON = "read_only"
    STANDARD_TESTING = "standard"
    ELEVATED_APPROVAL = "elevated"
    FULL_LAB = "full_lab"
    UNRESTRICTED = "unrestricted"


class FindingState(str, Enum):
    HYPOTHESIS = "hypothesis"
    NEEDS_EVIDENCE = "needs_evidence"
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    FALSE_POSITIVE = "false_positive"
    ACCEPTED_RISK = "accepted_risk"
    FIXED = "fixed"
    RETEST_PASSED = "retest_passed"


class AssessmentPhase(str, Enum):
    INTAKE = "intake"
    RECON = "recon"
    STATIC = "static"
    DYNAMIC = "dynamic"
    HYPOTHESIS = "hypothesis"
    VERIFY = "verify"
    REPORT = "report"
    STOPPED = "stopped"


@dataclass
class Scope:
    project: str = ""
    target: str = ""
    environment: str = "unknown"
    authorized: bool = False
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    test_accounts: list[str] = field(default_factory=list)
    rate_limit_rps: float = 2.0
    forbidden: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "target": self.target,
            "environment": self.environment,
            "authorized": self.authorized,
            "in_scope": self.in_scope,
            "out_of_scope": self.out_of_scope,
            "test_accounts": self.test_accounts,
            "rate_limit_rps": self.rate_limit_rps,
            "forbidden": self.forbidden,
            "notes": self.notes,
        }


@dataclass
class LiveHost:
    url: str
    status: str = ""
    title: str = ""
    tech: list[str] = field(default_factory=list)
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "status": self.status, "title": self.title, "tech": self.tech, "raw": self.raw}


@dataclass
class ParamCandidate:
    url: str
    param: str = ""
    method: str = "GET"

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "param": self.param, "method": self.method}


@dataclass
class SecretHit:
    source: str
    secret_type: str
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "secret_type": self.secret_type, "snippet": self.snippet}


@dataclass
class AttackSurface:
    target: str = ""
    subdomains: list[str] = field(default_factory=list)
    live_hosts: list[LiveHost] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    api_endpoints: list[str] = field(default_factory=list)
    parameters: list[ParamCandidate] = field(default_factory=list)
    js_files: list[str] = field(default_factory=list)
    secrets: list[SecretHit] = field(default_factory=list)
    nuclei: list[str] = field(default_factory=list)
    tech_stack: dict[str, str] = field(default_factory=dict)
    phase_artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "subdomains": self.subdomains,
            "live_hosts": [h.to_dict() for h in self.live_hosts],
            "urls": self.urls,
            "api_endpoints": self.api_endpoints,
            "parameters": [p.to_dict() for p in self.parameters],
            "js_files": self.js_files,
            "secrets": [s.to_dict() for s in self.secrets],
            "nuclei": self.nuclei,
            "tech_stack": self.tech_stack,
            "phase_artifacts": self.phase_artifacts,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "subdomains": len(self.subdomains),
            "live_hosts": len(self.live_hosts),
            "urls": len(self.urls),
            "api_endpoints": len(self.api_endpoints),
            "parameters": len(self.parameters),
            "js_files": len(self.js_files),
            "secrets": len(self.secrets),
            "nuclei": len(self.nuclei),
        }

    def flat_observations(self, limit: int = 100) -> list[str]:
        items: list[str] = []
        for h in self.live_hosts[:30]:
            items.append(h.raw or h.url)
        items.extend(self.urls[:40])
        items.extend(self.api_endpoints[:20])
        items.extend(self.nuclei[:10])
        return items[:limit]


@dataclass
class Evidence:
    id: str
    source_tool: str
    evidence_type: str
    content: str
    severity_hint: str = "info"
    task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_tool": self.source_tool,
            "evidence_type": self.evidence_type,
            "content": self.content,
            "severity_hint": self.severity_hint,
            "task_id": self.task_id,
        }


@dataclass
class ExecutorTask:
    id: str
    goal: str
    target: str
    permission_required: PermissionMode
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    command_plan: list[dict[str, str]] = field(default_factory=list)
    expected_evidence: list[str] = field(default_factory=list)
    evidence_schema: dict[str, Any] = field(default_factory=dict)
    stop_condition: str = ""
    success_criteria: str = ""
    approval_needed: bool = False
    module: str = "dynamic"  # static|dynamic|fuzzer|dependency|log

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutorTask:
        perm = data.get("permission_required", "read_only")
        if isinstance(perm, str):
            perm = PermissionMode(perm) if perm in {p.value for p in PermissionMode} else PermissionMode.READ_ONLY_RECON
        evidence_schema = data.get("evidence_schema", {})
        if isinstance(evidence_schema, str):
            evidence_schema = {"type": evidence_schema, "proof": "manual"}
        return cls(
            id=data.get("id", f"TASK-{uuid4().hex[:8]}"),
            goal=data.get("goal", ""),
            target=data.get("target", ""),
            permission_required=perm,
            allowed_tools=data.get("allowed_tools", []),
            forbidden_actions=data.get("forbidden_actions", []),
            command_plan=data.get("command_plan", []),
            expected_evidence=data.get("expected_evidence", []),
            evidence_schema=evidence_schema,
            stop_condition=data.get("stop_condition", ""),
            success_criteria=data.get("success_criteria", ""),
            approval_needed=data.get("approval_needed", False),
            module=data.get("module", "dynamic"),
        )


class PlanHypothesis(BaseModel):
    id: str = ""
    title: str = ""
    component: str = ""
    assumption: str = ""
    why_suspicious: str = ""
    evidence_needed: str = ""
    safe_test: str = ""
    impact_if_true: str = ""
    priority: int = 5


class KilledHypothesis(BaseModel):
    id: str = ""
    title: str = ""
    reason: str = ""


class PlanOutput(BaseModel):
    """Structured planner output from multi-agent debate."""

    hypotheses: list[PlanHypothesis] = Field(default_factory=list)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    chains: list[dict[str, Any]] = Field(default_factory=list)
    killed: list[KilledHypothesis] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.5
    planner_summary: str = ""
    raw: str = ""
    model: str = ""
    tech_profile: str = "generic_web"


@dataclass
class ExecutorResult:
    task_id: str
    status: str
    permission_check: dict[str, Any]
    commands_used: list[str]
    output_summary: str
    evidence: list[str]
    observations: list[str]
    risk: str
    recommended_next_step: str
    errors: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "permission_check": self.permission_check,
            "commands_used": self.commands_used,
            "output_summary": self.output_summary,
            "evidence": self.evidence,
            "observations": self.observations,
            "risk": self.risk,
            "recommended_next_step": self.recommended_next_step,
            "errors": self.errors,
        }


@dataclass
class Hypothesis:
    id: str
    title: str
    component: str
    assumption: str
    why_suspicious: str
    evidence_needed: str
    safe_test: str
    impact_if_true: str
    priority: int = 5
    state: str = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "component": self.component,
            "assumption": self.assumption,
            "why_suspicious": self.why_suspicious,
            "evidence_needed": self.evidence_needed,
            "safe_test": self.safe_test,
            "impact_if_true": self.impact_if_true,
            "priority": self.priority,
            "state": self.state,
        }


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    state: FindingState
    component: str
    affected_asset: str
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    impact: str = ""
    root_cause: str = ""
    remediation: str = ""
    cwe: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "state": self.state.value if isinstance(self.state, FindingState) else self.state,
            "component": self.component,
            "affected_asset": self.affected_asset,
            "description": self.description,
            "evidence": self.evidence,
            "impact": self.impact,
            "root_cause": self.root_cause,
            "remediation": self.remediation,
            "cwe": self.cwe,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SessionState:
    """Persisted engagement state for resume after token/context limits."""

    session_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    scope: Scope = field(default_factory=Scope)
    permission_mode: PermissionMode = PermissionMode.READ_ONLY_RECON
    current_phase: AssessmentPhase = AssessmentPhase.INTAKE
    known_assets: list[str] = field(default_factory=list)
    attack_surface: list[str] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    task_queue: list[dict[str, Any]] = field(default_factory=list)
    completed_tasks: list[dict[str, Any]] = field(default_factory=list)
    conversation: list[dict[str, str]] = field(default_factory=list)
    planner_summary: str = ""
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "scope": self.scope.to_dict(),
            "permission_mode": self.permission_mode.value,
            "current_phase": self.current_phase.value,
            "known_assets": self.known_assets,
            "attack_surface": self.attack_surface,
            "hypotheses": self.hypotheses,
            "findings": self.findings,
            "task_queue": self.task_queue,
            "completed_tasks": self.completed_tasks,
            "conversation": self.conversation[-50:],
            "planner_summary": self.planner_summary,
            "pending_approvals": self.pending_approvals,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        scope_data = data.get("scope", {})
        scope = Scope(**{k: v for k, v in scope_data.items() if k in Scope.__dataclass_fields__})
        perm = data.get("permission_mode", "read_only")
        phase = data.get("current_phase", "intake")
        return cls(
            session_id=data.get("session_id", str(uuid4())),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            scope=scope,
            permission_mode=PermissionMode(perm) if perm in {p.value for p in PermissionMode} else PermissionMode.READ_ONLY_RECON,
            current_phase=AssessmentPhase(phase) if phase in {p.value for p in AssessmentPhase} else AssessmentPhase.INTAKE,
            known_assets=data.get("known_assets", []),
            attack_surface=data.get("attack_surface", []),
            hypotheses=data.get("hypotheses", []),
            findings=data.get("findings", []),
            task_queue=data.get("task_queue", []),
            completed_tasks=data.get("completed_tasks", []),
            conversation=data.get("conversation", []),
            planner_summary=data.get("planner_summary", ""),
            pending_approvals=data.get("pending_approvals", []),
            metadata=data.get("metadata", {}),
        )
