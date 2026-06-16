"""Test Planning Layer (flowchart node 5 / G).

Turns the cognition engine's risk-ranked hypotheses into a concrete, ordered,
budgeted test queue bound to vulnerability modules. It:
  - selects the right module + non-destructive test family per hypothesis,
  - gates by permission level and scope preconditions (e.g. IDOR needs two
    accounts; auth/upload need a test account),
  - allocates a request budget and assigns stop conditions,
  - emits ExecutorTask objects for the existing executor path.

Pure planning/data — it never sends a request. The executor and
PermissionManager remain the only components that act, within authorized scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from wth_ai.cognition.reasoning_engine import ReasoningReport
from wth_ai.models import ExecutorTask, PermissionMode, Scope
from wth_ai.permission_manager import PERMISSION_LEVEL
from wth_ai.planning import modules as mod


@dataclass
class PlannedTest:
    id: str
    rank: int
    module_id: str
    module_name: str
    vuln_class: str
    target: str
    family: str
    technique: str
    permission: str
    request_budget: int
    requires_auth: bool
    risk_score: float
    severity_band: str
    evidence_schema: dict[str, Any]
    stop_condition: str
    skipped: bool = False
    skip_reason: str = ""
    missing_preconditions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rank": self.rank,
            "module": self.module_id,
            "module_name": self.module_name,
            "vuln_class": self.vuln_class,
            "target": self.target,
            "family": self.family,
            "technique": self.technique,
            "permission": self.permission,
            "request_budget": self.request_budget,
            "requires_auth": self.requires_auth,
            "risk_score": self.risk_score,
            "severity_band": self.severity_band,
            "evidence_schema": self.evidence_schema,
            "stop_condition": self.stop_condition,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "missing_preconditions": self.missing_preconditions,
        }

    def to_executor_task(self) -> ExecutorTask:
        return ExecutorTask(
            id=self.id,
            goal=f"[{self.module_name}] {self.family}: {self.technique}",
            target=self.target,
            permission_required=PermissionMode(self.permission),
            allowed_tools=["httpx", "curl", "diff_brain"],
            evidence_schema=self.evidence_schema,
            module="dynamic",
            stop_condition=self.stop_condition,
            success_criteria=self.evidence_schema.get("proof", ""),
        )


@dataclass
class TestPlan:
    tests: list[PlannedTest] = field(default_factory=list)
    total_request_budget: int = 0
    request_budget_limit: int = 0
    permission_mode: str = "read_only"

    def executable(self) -> list[PlannedTest]:
        return [t for t in self.tests if not t.skipped]

    def skipped_tests(self) -> list[PlannedTest]:
        return [t for t in self.tests if t.skipped]

    def to_executor_tasks(self) -> list[ExecutorTask]:
        return [t.to_executor_task() for t in self.executable()]

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "permission_mode": self.permission_mode,
            "request_budget_used": self.total_request_budget,
            "request_budget_limit": self.request_budget_limit,
            "executable": len(self.executable()),
            "skipped": len(self.skipped_tests()),
            "tests": [t.to_dict() for t in self.tests],
        }

    def format_cli(self) -> str:
        execu = self.executable()
        if not self.tests:
            return (
                "## Test Plan\n"
                "Tidak ada hipotesis untuk direncanakan. Jalankan `/recon` lalu `/reason` dulu."
            )
        lines = [
            "## Test Plan (module-bound, non-destructive)",
            f"- Permission: `{self.permission_mode}` · "
            f"Budget: {self.total_request_budget}/{self.request_budget_limit} req · "
            f"Executable: {len(execu)} · Skipped: {len(self.skipped_tests())}",
            "",
            "### Queue",
        ]
        for t in execu:
            auth = " [auth]" if t.requires_auth else ""
            lines.append(
                f"{t.rank}. [{t.severity_band.upper()} {t.risk_score:.0f}] "
                f"{t.module_name} · `{t.family}`{auth} → {t.target[:60]}"
            )
            lines.append(f"   - {t.technique} (budget {t.request_budget} req; stop: {t.stop_condition})")
        skipped = self.skipped_tests()
        if skipped:
            lines.append("")
            lines.append("### Skipped (gated)")
            for t in skipped:
                lines.append(f"- {t.module_name} · {t.vuln_class}: {t.skip_reason}")
        return "\n".join(lines)


class TestPlanner:
    """Build a gated, budgeted test queue from reasoned hypotheses."""

    def __init__(self, *, default_budget: int = 120, max_tests: int = 14) -> None:
        self.default_budget = default_budget
        self.max_tests = max_tests

    def build_plan(
        self,
        report: ReasoningReport,
        *,
        scope: Scope | None = None,
        permission_mode: PermissionMode = PermissionMode.READ_ONLY_RECON,
        request_budget: int | None = None,
        max_tests: int | None = None,
    ) -> TestPlan:
        budget_limit = request_budget if request_budget is not None else self.default_budget
        cap = max_tests if max_tests is not None else self.max_tests
        accounts = len(scope.test_accounts) if scope else 0
        have_level = PERMISSION_LEVEL[permission_mode]

        plan = TestPlan(
            request_budget_limit=budget_limit,
            permission_mode=permission_mode.value,
        )
        budget_used = 0

        for item in report.items[:cap]:
            hyp = item.hypothesis
            vuln_class = (hyp.get("vuln_class") or "unknown").lower().replace("-", "_")
            module = mod.module_for_vuln_class(vuln_class)
            target = hyp.get("component") or hyp.get("url") or (scope.target if scope else "")
            if module is None:
                plan.tests.append(self._skipped(
                    item, vuln_class, target, "unmapped",
                    skip_reason=f"no module covers vuln class '{vuln_class}'",
                ))
                continue

            families = module.non_destructive_families()
            if not families:
                plan.tests.append(self._skipped(
                    item, vuln_class, target, module.id,
                    skip_reason="no non-destructive family available",
                    module=module,
                ))
                continue
            family = families[0]

            # Precondition gating.
            missing = self._missing_preconditions(module, accounts)
            if missing:
                plan.tests.append(self._skipped(
                    item, vuln_class, target, module.id,
                    skip_reason="missing precondition: " + ", ".join(missing),
                    module=module, family=family, missing=missing,
                ))
                continue

            # Permission gating (never auto-escalates — just defers).
            if PERMISSION_LEVEL[module.permission] > have_level:
                plan.tests.append(self._skipped(
                    item, vuln_class, target, module.id,
                    skip_reason=f"needs permission '{module.permission.value}' (have '{permission_mode.value}')",
                    module=module, family=family,
                ))
                continue

            # Budget gating.
            if budget_used + family.request_cost > budget_limit:
                plan.tests.append(self._skipped(
                    item, vuln_class, target, module.id,
                    skip_reason="request budget exhausted",
                    module=module, family=family,
                ))
                continue

            budget_used += family.request_cost
            plan.tests.append(PlannedTest(
                id=f"PT-{uuid4().hex[:8]}",
                rank=item.rank,
                module_id=module.id,
                module_name=module.name,
                vuln_class=vuln_class,
                target=target,
                family=family.name,
                technique=family.technique,
                permission=module.permission.value,
                request_budget=family.request_cost,
                requires_auth=family.requires_auth,
                risk_score=item.risk.score,
                severity_band=item.risk.severity_band,
                evidence_schema=module.evidence_schema(vuln_class),
                stop_condition=module.default_stop,
            ))

        plan.total_request_budget = budget_used
        return plan

    @staticmethod
    def _missing_preconditions(module: mod.VulnModule, accounts: int) -> list[str]:
        missing: list[str] = []
        for pc in module.preconditions:
            if pc == mod.PRECOND_TWO_ACCOUNTS and accounts < 2:
                missing.append("two test accounts (accounts=userA:pw,userB:pw)")
            elif pc == mod.PRECOND_TEST_ACCOUNT and accounts < 1:
                missing.append("a test account (accounts=user:pw)")
        return missing

    @staticmethod
    def _skipped(
        item: Any,
        vuln_class: str,
        target: str,
        module_id: str,
        *,
        skip_reason: str,
        module: mod.VulnModule | None = None,
        family: mod.TestFamily | None = None,
        missing: list[str] | None = None,
    ) -> PlannedTest:
        return PlannedTest(
            id=f"PT-{uuid4().hex[:8]}",
            rank=item.rank,
            module_id=module_id,
            module_name=module.name if module else module_id,
            vuln_class=vuln_class,
            target=target,
            family=family.name if family else "-",
            technique=family.technique if family else "",
            permission=module.permission.value if module else "read_only",
            request_budget=0,
            requires_auth=family.requires_auth if family else False,
            risk_score=item.risk.score,
            severity_band=item.risk.severity_band,
            evidence_schema=module.evidence_schema(vuln_class) if module else {},
            stop_condition=module.default_stop if module else "",
            skipped=True,
            skip_reason=skip_reason,
            missing_preconditions=missing or [],
        )
