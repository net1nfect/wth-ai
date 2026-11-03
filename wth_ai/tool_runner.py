"""Safe command execution with permission and scope checks."""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any, TYPE_CHECKING

from wth_ai.config import WTHConfig
from wth_ai.models import ExecutorResult, ExecutorTask, PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager

if TYPE_CHECKING:
    from wth_ai.evidence.vault import EvidenceVault


class ToolRunner:
    """Execute approved commands in subprocess with safety controls."""

    def __init__(
        self,
        config: WTHConfig,
        permission: PermissionManager,
        scope: ScopeManager,
    ) -> None:
        self.config = config
        self.permission = permission
        self.scope = scope
        # WU-117 (PRD-009): set post-construction by callers (orchestrator.py,
        # jobs/orchestrator_bridge.py) -- not a constructor param because both
        # callers build EvidenceVault AFTER ToolRunner in their init order.
        self.evidence_vault: "EvidenceVault | None" = None

    def execute_task(
        self,
        task: ExecutorTask,
        *,
        approved: bool = False,
        dry_run: bool = False,
    ) -> ExecutorResult:
        in_scope, scope_reason = self.scope.is_in_scope(task.target)
        perm_ok = self.permission.mode_sufficient(task.permission_required)
        risk = "low"

        if not in_scope:
            return self._blocked(task, f"Out of scope: {scope_reason}", risk)
        if not perm_ok:
            return self._blocked(
                task,
                f"Permission insufficient: need {task.permission_required.value}, have {self.permission.mode.value}",
                "medium",
            )
        if task.approval_needed and not approved:
            approval_id = f"needs /approve for task {task.id}"
            return ExecutorResult(
                task_id=task.id,
                status="needs_approval",
                permission_check=self.permission.permission_check_block(
                    task.permission_required, "high", approved=False
                ),
                commands_used=[],
                output_summary=f"Task membutuhkan approval eksplisit. {approval_id}",
                evidence=[],
                observations=[scope_reason],
                risk="high",
                recommended_next_step=f"User jalankan /approve {task.id} jika setuju",
            )

        commands_used: list[str] = []
        all_output: list[str] = []
        evidence: list[str] = []
        observations: list[str] = []
        errors: list[str] = []
        max_risk = "low"

        plans = task.command_plan or [{"command": "", "purpose": task.goal}]
        for plan in plans:
            command = plan.get("command", "").strip()
            if not command:
                continue
            # P0 exit-review fix (HIGH): thread `approved` -- lihat catatan
            # sama di terminal/bridge.py::run_shell().
            check = self.permission.check_command(
                command, required_mode=task.permission_required, approved=approved,
            )
            if check["risk"] in {"high", "critical"}:
                max_risk = check["risk"]
            if not check["allowed"]:
                return self._blocked(task, check["reason"], check["risk"])
            if check.get("approval_needed") and not approved:
                return ExecutorResult(
                    task_id=task.id,
                    status="needs_approval",
                    permission_check=self.permission.permission_check_block(
                        task.permission_required, check["risk"], approved=False
                    ),
                    commands_used=commands_used,
                    output_summary=f"Command blocked pending approval: {command[:100]}",
                    evidence=evidence,
                    observations=observations,
                    risk=check["risk"],
                    recommended_next_step=f"/approve {task.id}",
                )

            if dry_run:
                commands_used.append(f"[DRY-RUN] {command}")
                observations.append(f"Would run: {command}")
                continue

            output, err, code = self._run_command(command)
            commands_used.append(command)
            if output:
                redacted = self.scope.redact_secrets(output[: self.config.tools.max_output_bytes])
                all_output.append(redacted)
                evidence.extend(self._extract_evidence(redacted, command))
            if err:
                errors.append(self.scope.redact_secrets(err[:5000]))
            observations.append(f"Exit code {code} for: {command.split()[0] if command else 'cmd'}")

            # WU-117 (PRD-009): stop_condition dead field sebelumnya (LLM
            # tetap diminta mengisi tiap plan, tapi tidak pernah dibaca kode
            # manapun untuk keputusan). Wiring: simpan pair request/response
            # ke evidence_vault tiap command (sebelumnya TIDAK PERNAH
            # tersimpan dari jalur ToolRunner ini sama sekali -- gap
            # tersendiri, sekaligus tertutup), lalu kalau stop_condition
            # diisi DAN evidence_schema task sudah terpenuhi, hentikan sisa
            # command_plan lebih awal alih-alih tetap menjalankan semuanya.
            if self.evidence_vault is not None:
                self.evidence_vault.store(
                    finding_id=task.id,
                    request={"command": command},
                    response={"stdout": (output or "")[:2000], "stderr": (err or "")[:2000], "exit_code": code},
                    evidence_schema=task.evidence_schema,
                )
                if task.stop_condition and task.evidence_schema:
                    satisfied, _missing = self.evidence_vault.validate_schema(task.id, task.evidence_schema)
                    if satisfied:
                        observations.append(f"stop_condition met early: {task.stop_condition}")
                        time.sleep(1.0 / max(self.scope.scope.rate_limit_rps, 0.1))
                        break

            time.sleep(1.0 / max(self.scope.scope.rate_limit_rps, 0.1))

        status = "success" if not errors else "failed"
        return ExecutorResult(
            task_id=task.id,
            status=status,
            permission_check=self.permission.permission_check_block(
                task.permission_required, max_risk, approved=approved
            ),
            commands_used=commands_used,
            output_summary=self._summarize(all_output, task),
            evidence=evidence[:20],
            observations=observations,
            risk=max_risk,
            recommended_next_step="Kirim hasil ke Planner untuk update hipotesis",
            errors="; ".join(errors) if errors else "none",
        )

    def _run_command(self, command: str) -> tuple[str, str, int]:
        from wth_ai.shell_runner import run_command

        return run_command(
            command,
            timeout=self.config.tools.timeout_seconds,
            max_bytes=self.config.tools.max_output_bytes,
        )

    def _extract_evidence(self, output: str, command: str) -> list[str]:
        evidence = []
        for line in output.splitlines()[:50]:
            line = line.strip()
            if not line:
                continue
            if any(sig in line.lower() for sig in (
                "route::", "middleware", "vulnerable", "cve-", "http/", "status",
                "error", "warning", "id:", "unauthorized", "forbidden", "200", "403", "401", "500",
            )):
                evidence.append(line[:300])
        if not evidence and output.strip():
            evidence.append(output.strip()[:500])
        return evidence[:10]

    def _summarize(self, outputs: list[str], task: ExecutorTask) -> str:
        if not outputs:
            return f"Task {task.id} selesai tanpa output signifikan."
        combined = "\n".join(outputs)
        lines = [l for l in combined.splitlines() if l.strip()]
        preview = "\n".join(lines[:15])
        if len(lines) > 15:
            preview += f"\n... ({len(lines) - 15} baris lagi)"
        return preview[:3000]

    def _blocked(self, task: ExecutorTask, reason: str, risk: str) -> ExecutorResult:
        return ExecutorResult(
            task_id=task.id,
            status="blocked",
            permission_check=self.permission.permission_check_block(task.permission_required, risk),
            commands_used=[],
            output_summary=reason,
            evidence=[],
            observations=[],
            risk=risk,
            recommended_next_step="Perbaiki scope/permission atau minta approval",
            errors=reason,
        )
