"""`Engagement` — authorization-lifecycle aggregate root (WU-014, ADR-011, PRD-003).

SKELETON ONLY (P0 slice of PRD-003 — "FSM + entity dasar"; store penuh
(`EngagementStore`), `/engagement` CLI commands, `ExecutionBroker`
fetch-fresh integration, dan migrasi `Scope`->`Engagement` semuanya P1,
BELUM dikerjakan di WU ini). Kelas ini TIDAK dipakai di manapun lain di
codebase saat ini secara sengaja -- `SessionState` belum dapat field
`engagement_id`, tidak ada command yang meng-create/authorize Engagement.
Ini murni data model + FSM yang akan dikonsumsi WU-101 (EngagementStore) dan
WU-102 (ExecutionBroker fetch-fresh enforcement, lapis kedua per ADR-011).

Beda risk profile dari `wth_ai.loop.state_machine.LoopStateMachine`
(workflow-phase, in-process, single-instance) -- lihat ADR-011 §Naming
Collision. `Engagement` di sini adalah authorization-lifecycle,
lintas-proses, security-critical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

# Agent-actor identifiers -- SAMA PERSIS dengan wth_ai.agents.tool_registry.ACTOR_TOOLS
# kunci (supervisor/planner/executor/recon). Transisi ke ACTIVE tidak pernah
# boleh diatribusikan ke actor ini (ADR-011, PRD-003 acceptance criterion:
# "Engagement.transition('activate', actor='supervisor') HARUS gagal").
AGENT_ACTOR_NAMES: frozenset[str] = frozenset({"supervisor", "planner", "executor", "recon"})


class EngagementStatus(str, Enum):
    DRAFT = "draft"
    VALIDATING = "validating"
    READY = "ready"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"
    EXPIRED = "expired"


_TERMINAL_STATES: frozenset[EngagementStatus] = frozenset(
    {EngagementStatus.COMPLETED, EngagementStatus.ABORTED, EngagementStatus.EXPIRED}
)

# event -> {from_status: to_status}. abort/killswitch ditangani terpisah di
# transition() (berlaku dari SEMUA state non-terminal, bukan cuma satu).
_TRANSITIONS: dict[str, dict[EngagementStatus, EngagementStatus]] = {
    "submit_for_validation": {EngagementStatus.DRAFT: EngagementStatus.VALIDATING},
    "validation_failed": {EngagementStatus.VALIDATING: EngagementStatus.DRAFT},
    "validation_passed": {EngagementStatus.VALIDATING: EngagementStatus.READY},
    "activate": {EngagementStatus.READY: EngagementStatus.ACTIVE},
    "pause": {EngagementStatus.ACTIVE: EngagementStatus.PAUSED},
    "resume": {EngagementStatus.PAUSED: EngagementStatus.ACTIVE},
    "finalize": {
        EngagementStatus.ACTIVE: EngagementStatus.COMPLETED,
        EngagementStatus.PAUSED: EngagementStatus.COMPLETED,
    },
}


@dataclass
class TransitionResult:
    success: bool
    status: EngagementStatus
    reason: str = ""


@dataclass
class TargetScope:
    pattern: str  # syntax SAMA dengan ScopeManager._host_matches_pattern
    asset_type: str = "host"  # host|url_prefix|cidr|api_base
    methods: list[str] = field(default_factory=lambda: ["GET", "HEAD"])
    notes: str = ""


@dataclass
class AuthorizationSource:
    kind: str  # bug_bounty_program|signed_saw|written_client_authorization|lab_disposable|ctf_platform_rules
    reference: str
    verified_by: str  # HARUS actor manusia -- tidak divalidasi di sini (guard ada di transition())
    verified_at: str


@dataclass
class RateLimitPolicy:
    default_rps: float = 2.0
    per_capability: dict[str, float] = field(default_factory=dict)


@dataclass
class ConcurrencyPolicy:
    max_parallel_executions: int = 1
    max_parallel_per_target: int = 1


@dataclass
class TimeWindow:
    not_before: str | None = None
    expires_at: str | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now >= expires


class Engagement:
    """Class biasa, BUKAN @dataclass (ADR-011 §enforcement): `status` harus
    read-only property, `transition()` satu-satunya mutator -- sama disiplin
    dengan `LoopStateMachine` (WU-006), enforcement-by-construction bukan
    konvensi."""

    def __init__(
        self,
        *,
        name: str,
        created_by: str,
        targets: list[TargetScope] | None = None,
        excluded_assets: list[str] | None = None,
        allowed_operations: list[str] | None = None,
        forbidden_operations: list[str] | None = None,
        permission_profile: str = "read_only_recon",
        rate_limits: RateLimitPolicy | None = None,
        concurrency: ConcurrencyPolicy | None = None,
        time_window: TimeWindow | None = None,
        credentials_ref: list[str] | None = None,
        evidence_storage: str = "",
        audit_ref: str = "",
        notes: str = "",
        id: str | None = None,
    ) -> None:
        self.id = id or f"ENG-{uuid4().hex[:8]}"
        self.name = name
        self.created_by = created_by
        now = datetime.now(timezone.utc).isoformat()
        self.created_at = now
        self.updated_at = now
        self.authorization_source: AuthorizationSource | None = None
        self.approved_by: str | None = None
        self.targets: list[TargetScope] = targets or []
        self.excluded_assets: list[str] = excluded_assets or []
        self.allowed_operations: list[str] = allowed_operations or []
        self.forbidden_operations: list[str] = forbidden_operations or []
        self.permission_profile = permission_profile
        self.rate_limits = rate_limits or RateLimitPolicy()
        self.concurrency = concurrency or ConcurrencyPolicy()
        self.time_window = time_window or TimeWindow()
        self.credentials_ref: list[str] = credentials_ref or []
        self.evidence_storage = evidence_storage
        self.audit_ref = audit_ref
        self.notes = notes
        self._status = EngagementStatus.DRAFT

    @classmethod
    def from_scope(cls, scope: Any, *, created_by: str, name: str = "") -> "Engagement":
        """WU-112 Fase A (compat shim, ADR-011/ADR-012): derive a DRAFT
        `Engagement` from the legacy `Scope` value object (`wth_ai.models.
        Scope`), mapping its BOUNDARY data so an Engagement can be created
        alongside an existing session without rewriting the ~105 `state.scope`
        call sites (that is Fase B, deliberately deferred -- see WORK-UNITS.md
        WU-112).

        CRITICAL decoupling preserved (the whole point of ADR-012): this maps
        ONLY scope-boundary data (targets/exclusions/rate/notes). It does NOT
        touch `authorization_source`/`approved_by`/`status`. In particular
        `scope.authorized=True` is NOT translated into an activated or
        human-approved Engagement -- scope-authorization (WU-003 dimension 2)
        and engagement lifecycle-authorization (dimension, activated only by a
        human `transition('activate', ...)`) are SEPARATE dimensions by
        design. The result is always a fresh DRAFT that must still walk the
        FSM; auto-activating from a boolean would re-collapse the exact
        cross-dimension coupling ADR-012 split apart.
        """
        targets: list[TargetScope] = []
        seen: set[str] = set()
        primary = getattr(scope, "target", "") or ""
        if primary:
            targets.append(TargetScope(pattern=primary, asset_type="host"))
            seen.add(primary)
        for pat in getattr(scope, "in_scope", []) or []:
            if pat and pat not in seen:
                targets.append(TargetScope(pattern=pat, asset_type="host"))
                seen.add(pat)
        excluded: list[str] = []
        for pat in (list(getattr(scope, "out_of_scope", []) or []) + list(getattr(scope, "forbidden", []) or [])):
            if pat and pat not in excluded:
                excluded.append(pat)
        rps = float(getattr(scope, "rate_limit_rps", 2.0) or 2.0)
        return cls(
            name=name or getattr(scope, "project", "") or primary or "engagement-from-scope",
            created_by=created_by,
            targets=targets,
            excluded_assets=excluded,
            rate_limits=RateLimitPolicy(default_rps=rps),
            notes=getattr(scope, "notes", "") or "",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Engagement":
        """Reconstruct an `Engagement` from `to_dict()`'s output -- used by
        `EngagementStore` (WU-101) to hydrate rows fetched from storage.

        Bypasses the FSM's transition guards intentionally: this is loading
        PREVIOUSLY-VALIDATED state (it already passed those guards the first
        time a `.transition()` call produced it), not performing a new
        transition. `__init__` itself is deliberately NOT extended with a
        `status=` override -- keeping "fresh construction" and "storage
        hydration" as two distinct, clearly-named entry points is safer than
        one constructor silently supporting both.
        """
        eng = cls(
            name=data["name"],
            created_by=data["created_by"],
            targets=[TargetScope(**t) for t in data.get("targets", [])],
            excluded_assets=data.get("excluded_assets", []),
            allowed_operations=data.get("allowed_operations", []),
            forbidden_operations=data.get("forbidden_operations", []),
            permission_profile=data.get("permission_profile", "read_only_recon"),
            rate_limits=RateLimitPolicy(**data["rate_limits"]) if data.get("rate_limits") else None,
            concurrency=ConcurrencyPolicy(**data["concurrency"]) if data.get("concurrency") else None,
            time_window=TimeWindow(**data["time_window"]) if data.get("time_window") else None,
            credentials_ref=data.get("credentials_ref", []),
            evidence_storage=data.get("evidence_storage", ""),
            audit_ref=data.get("audit_ref", ""),
            notes=data.get("notes", ""),
            id=data["id"],
        )
        eng.created_at = data.get("created_at", eng.created_at)
        eng.updated_at = data.get("updated_at", eng.updated_at)
        eng.approved_by = data.get("approved_by")
        src = data.get("authorization_source")
        eng.authorization_source = AuthorizationSource(**src) if src else None
        eng._status = EngagementStatus(data.get("status", EngagementStatus.DRAFT.value))
        return eng

    @property
    def status(self) -> EngagementStatus:
        """Read-only. EXPIRED dihitung otomatis dari time_window.expires_at
        untuk state ACTIVE/PAUSED (STATE-MACHINES.md §1: "otomatis") --
        skeleton ini tidak punya store/cron, jadi dicek on-read, bukan
        di-persist sebagai mutasi terpisah."""
        if self._status in (EngagementStatus.ACTIVE, EngagementStatus.PAUSED) and self.time_window.is_expired():
            return EngagementStatus.EXPIRED
        return self._status

    def transition(self, event: str, actor: str) -> TransitionResult:
        """SATU-SATUNYA mutator (ADR-011). Tidak ada setter publik untuk
        `status`/`_status` -- lihat catatan kelas."""
        current = self.status  # via property -- menangkap auto-EXPIRED

        if current in _TERMINAL_STATES:
            return TransitionResult(False, current, f"Engagement sudah terminal ({current.value})")

        if event in ("abort", "killswitch"):
            # Selalu boleh dari state non-terminal manapun (fail-safe: gampang
            # berhenti, susah lanjut) -- STATE-MACHINES.md §1.
            self._status = EngagementStatus.ABORTED
            self.updated_at = datetime.now(timezone.utc).isoformat()
            return TransitionResult(True, self._status)

        if event not in _TRANSITIONS:
            return TransitionResult(False, current, f"Unknown event: {event}")

        target = _TRANSITIONS[event].get(current)
        if target is None:
            return TransitionResult(
                False, current, f"Event '{event}' tidak valid dari state '{current.value}'"
            )

        # Guard per transisi (STATE-MACHINES.md §1).
        if event == "submit_for_validation" and not self.targets:
            return TransitionResult(False, current, "DRAFT->VALIDATING butuh minimal 1 target")

        if event == "validation_passed":
            if self.authorization_source is None:
                return TransitionResult(False, current, "VALIDATING->READY butuh authorization_source")
            conflict = set(self.allowed_operations) & set(self.forbidden_operations)
            if conflict:
                return TransitionResult(
                    False, current, f"allowed_operations/forbidden_operations konflik: {conflict}"
                )

        if event == "activate":
            if actor in AGENT_ACTOR_NAMES:
                return TransitionResult(
                    False, current,
                    f"READY->ACTIVE ditolak: actor '{actor}' adalah agent, bukan manusia",
                )
            if self.approved_by is None:
                return TransitionResult(False, current, "READY->ACTIVE butuh approved_by (manusia)")
            if self.approved_by in AGENT_ACTOR_NAMES:
                return TransitionResult(
                    False, current,
                    f"READY->ACTIVE ditolak: approved_by='{self.approved_by}' adalah agent, bukan manusia",
                )

        self._status = target
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return TransitionResult(True, self._status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "authorization_source": (
                self.authorization_source.__dict__ if self.authorization_source else None
            ),
            "approved_by": self.approved_by,
            "targets": [t.__dict__ for t in self.targets],
            "excluded_assets": self.excluded_assets,
            "allowed_operations": self.allowed_operations,
            "forbidden_operations": self.forbidden_operations,
            "permission_profile": self.permission_profile,
            "rate_limits": self.rate_limits.__dict__,
            "concurrency": self.concurrency.__dict__,
            "time_window": self.time_window.__dict__,
            "credentials_ref": self.credentials_ref,
            "evidence_storage": self.evidence_storage,
            "audit_ref": self.audit_ref,
            "notes": self.notes,
        }
