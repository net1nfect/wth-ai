"""Executor / Testing Brain — Qwen3 Coder."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from wth_ai.agents import BaseLLMAgent, load_prompt
from wth_ai.agents.tool_loop import AgentToolLoop
from wth_ai.config import WTHConfig
from wth_ai.executor.evidence import EvidencePipeline
from wth_ai.executor.modules import MODULES
from wth_ai.executor.sandbox import Sandbox
from wth_ai.executor.vuln import VULN_EXECUTORS
from wth_ai.models import ExecutorResult, ExecutorTask, SessionState
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.tool_runner import ToolRunner
from wth_ai.tools.runner import ManifestToolRunner

if TYPE_CHECKING:
    from wth_ai.evidence.vault import EvidenceVault
    from wth_ai.graph.asset_graph import AssetGraph
    from wth_ai.terminal.bridge import AgentTerminalBridge


class ExecutorAgent(BaseLLMAgent):
    """Translate tasks to module execution, sandbox, parse results."""

    def __init__(
        self,
        config: WTHConfig,
        client: LLMProviderRouter,
        tool_runner: ToolRunner,
        manifest_runner: ManifestToolRunner | None = None,
        *,
        terminal: AgentTerminalBridge | None = None,
        evidence_vault: EvidenceVault | None = None,
        asset_graph: AssetGraph | None = None,
    ) -> None:
        super().__init__(config, client)
        self.tool_runner = tool_runner
        self.manifest_runner = manifest_runner
        self.evidence = EvidencePipeline()
        self.system_prompt = load_prompt(config, "executor_system.txt")
        self.terminal = terminal
        self.evidence_vault = evidence_vault
        self.asset_graph = asset_graph

    def execute(
        self,
        task: ExecutorTask,
        state: SessionState,
        *,
        approved: bool = False,
        dry_run: bool = False,
    ) -> ExecutorResult:
        if task.module in MODULES and not dry_run:
            return self.execute_module(task, state, approved=approved)
        if not task.command_plan and self.client.available():
            task = self._enrich_task_from_llm(task, state)
        result = self.tool_runner.execute_task(task, approved=approved, dry_run=dry_run)
        if self.client.available() and result.status == "success":
            result = self._parse_with_llm(task, result, state)
        return result

    def execute_module(
        self,
        task: ExecutorTask,
        state: SessionState,
        *,
        approved: bool = False,
    ) -> ExecutorResult:
        if task.approval_needed and not approved:
            return ExecutorResult(
                task_id=task.id,
                status="needs_approval",
                permission_check={"allowed": False},
                commands_used=[],
                output_summary=f"Module {task.module} needs approval",
                evidence=[],
                observations=[],
                risk="high",
                recommended_next_step=f"/approve {task.id}",
            )
        if not task.target:
            task.target = state.scope.target or ""
        vuln_type = (task.evidence_schema or {}).get("type", "").lower()
        if vuln_type in VULN_EXECUTORS:
            from wth_ai.models import PermissionMode
            if state.permission_mode == PermissionMode.READ_ONLY_RECON:
                return ExecutorResult(
                    task_id=task.id, status="blocked", permission_check={"allowed": False},
                    commands_used=[], output_summary="Vuln modules require standard permission or higher",
                    evidence=[], observations=[], risk="medium",
                    recommended_next_step="/permission standard", errors="read_only",
                )
            # WU-012 (AGENT-02): idor/ssrf/xss/auth/race semuanya memanggil
            # requests.get()/SessionEngine.request() langsung dengan task.target
            # tanpa is_in_scope() sinkron -- task.target berasal dari planner
            # (LLM-proposed), jadi tanpa gate ini "LLM PROPOSES, POLICY DECIDES"
            # bocor: target di luar scope yang di-authorize bisa lolos ke HTTP
            # call nyata. execute_module() adalah titik konvergensi bersama
            # kelima vuln executor -- satu check di sini menutup celah untuk
            # semuanya sekaligus.
            from wth_ai.scope_manager import ScopeManager
            scope_mgr = ScopeManager()
            scope_mgr.scope = state.scope
            in_scope, scope_reason = scope_mgr.is_in_scope(task.target)
            if not in_scope:
                return ExecutorResult(
                    task_id=task.id, status="blocked", permission_check={"allowed": False},
                    commands_used=[], output_summary=f"Target out of scope: {scope_reason}",
                    evidence=[], observations=[], risk="medium",
                    recommended_next_step="/scope set target=<in-scope host> in_scope=<pattern>",
                    errors="out_of_scope",
                )
            try:
                executor_cls = VULN_EXECUTORS[vuln_type]
                return executor_cls(state).execute(task)
            except Exception as exc:
                return ExecutorResult(
                    task_id=task.id, status="failed", permission_check={},
                    commands_used=[f"vuln:{vuln_type}"], output_summary=str(exc),
                    evidence=[], observations=[], risk="low",
                    recommended_next_step="Check logs", errors=str(exc),
                )
        if self.terminal and self.client.available() and not task.command_plan:
            return self._execute_with_tools(task, state, approved=approved)

        # Module-path gate (review finding A1, P0). MODULES run
        # `task.command_plan` -- planner/LLM-authored strings -- through
        # ExecutorRunnerV2 -> shell_runner. That runner has no permission/scope/
        # policy dependency of its own, so without this block the module route
        # was the one path to subprocess with NO gate at all: a task in
        # READ_ONLY mode with an out-of-scope target ran its command_plan
        # verbatim (verified on the pre-fix code: `uname -a` and
        # `python3 -c print(42)` both executed despite `python3` being
        # REQUIRE_HUMAN in every mode). Ordered like `tool_runner.execute_task`.
        gate = self._gate_module_task(task, state)
        if gate is not None:
            return gate

        if not self.manifest_runner:
            return ExecutorResult(
                task_id=task.id, status="failed", permission_check={},
                commands_used=[], output_summary="Manifest runner unavailable",
                evidence=[], observations=[], risk="low",
                recommended_next_step="Check install", errors="no runner",
            )
        sandbox = Sandbox(self.config, task.id)
        try:
            mod_cls = MODULES.get(task.module, MODULES["dynamic"])
            mod = mod_cls(self.manifest_runner, self.config)
            result = mod.execute(task, sandbox)
        finally:
            sandbox.cleanup()
        return result

    def _gate_module_task(
        self, task: ExecutorTask, state: SessionState,
    ) -> ExecutorResult | None:
        """Scope + permission + per-command gate for the non-vuln module path.

        Returns a blocked/needs-approval result when the task must not run,
        else ``None``. Mirrors ``tool_runner.execute_task``'s ordering so both
        routes to subprocess enforce the same things.
        """
        from wth_ai.models import PermissionMode
        from wth_ai.permission_manager import PermissionManager
        from wth_ai.scope_manager import ScopeManager

        required = task.permission_required or PermissionMode.READ_ONLY_RECON

        scope_mgr = ScopeManager()
        scope_mgr.scope = state.scope
        target = task.target or state.scope.target or ""
        in_scope, scope_reason = scope_mgr.is_in_scope(target)
        if not in_scope:
            return ExecutorResult(
                task_id=task.id, status="blocked", permission_check={"allowed": False},
                commands_used=[], output_summary=f"Target out of scope: {scope_reason}",
                evidence=[], observations=[], risk="medium",
                recommended_next_step="/scope set target=<in-scope host> in_scope=<pattern>",
                errors="out_of_scope",
            )

        perm = PermissionManager(state.permission_mode)
        if not perm.mode_sufficient(required):
            return ExecutorResult(
                task_id=task.id, status="blocked", permission_check={"allowed": False},
                commands_used=[],
                output_summary=(
                    f"Module task needs permission {required.value}, "
                    f"have {state.permission_mode.value}"
                ),
                evidence=[], observations=[scope_reason], risk="medium",
                recommended_next_step=f"/permission {required.value}",
                errors="insufficient_permission",
            )

        for plan in (task.command_plan or [])[:5]:
            cmd = str(plan.get("command", ""))
            if not cmd:
                continue
            check = perm.check_command(cmd, required_mode=required)
            if not check.get("allowed"):
                return ExecutorResult(
                    task_id=task.id, status="blocked", permission_check={"allowed": False},
                    commands_used=[],
                    output_summary=f"Command blocked by policy: {check.get('reason')}",
                    evidence=[], observations=[], risk="high",
                    recommended_next_step="/permission elevated",
                    errors=str(check.get("reason")),
                )
        return None

    def _execute_with_tools(
        self, task: ExecutorTask, state: SessionState, *, approved: bool = False,
    ) -> ExecutorResult:
        if self.terminal is None:
            raise RuntimeError("terminal belum disiapkan untuk eksekusi tool")
        loop = AgentToolLoop(
            self.client, self.terminal, graph=self.asset_graph, vault=self.evidence_vault,
            max_iters=self.config.agent.max_iters,
        )
        prompt = (
            f"Execute this security task using run_shell and/or http_probe.\n"
            f"Goal: {task.goal}\nTarget: {task.target}\n"
            f"Evidence schema: {json.dumps(task.evidence_schema, ensure_ascii=False)}\n"
            f"Cite all findings with [TX:...]. Do not claim verified without proof."
        )
        result = loop.run(
            self.config.models.executor, self.system_prompt, prompt,
            actor="executor", backup=self.config.models.executor_backup,
            timeout=self.config.models.executor_timeout_seconds, approved=approved,
        )
        transcripts = result.get("transcripts", [])
        pairs = state.metadata.setdefault("request_pairs", [])
        for tid in transcripts:
            tx = self.terminal.transcripts.get(tid) if self.terminal else None
            if tx:
                pairs.append({
                    "method": tx.get("method", "GET"),
                    "url": tx.get("url") or tx.get("command", ""),
                    "response_status": tx.get("status_code", tx.get("exit_code")),
                    "body_snippet": (tx.get("stdout") or "")[:500],
                })
        return ExecutorResult(
            task_id=task.id,
            status="success" if result.get("grounded", True) else "needs_approval",
            permission_check={"mode": state.permission_mode.value, "allowed": True},
            commands_used=[f"tool_loop:{len(transcripts)} probes"],
            output_summary=result.get("content", "")[:4000],
            evidence=[f"[TX:{t}]" for t in transcripts],
            observations=result.get("violations", []),
            risk="medium",
            recommended_next_step="/verify",
            errors="none",
        )

    def _enrich_task_from_llm(self, task: ExecutorTask, state: SessionState) -> ExecutorTask:
        prompt = (
            f"Buat command plan aman untuk task ini.\n"
            f"Module: {task.module}\n"
            f"Permission: {state.permission_mode.value}\n"
            f"Scope target: {state.scope.target}\n"
            f"In scope: {state.scope.in_scope}\n\n"
            f"EXECUTOR_TASK:\n{json.dumps({
                'id': task.id, 'goal': task.goal, 'target': task.target,
                'module': task.module,
                'permission_required': task.permission_required.value,
                'allowed_tools': task.allowed_tools,
                'forbidden_actions': task.forbidden_actions,
            }, ensure_ascii=False)}\n\n"
            "Output format COMMAND_PLAN dengan command shell yang aman saja."
        )
        raw, _ = self._call(
            self.config.models.executor,
            self.config.models.executor_backup,
            self.system_prompt,
            [],
            prompt,
            role="executor",
            timeout=self.config.models.executor_timeout_seconds,
        )
        commands = re.findall(r"Command:\s*(.+)", raw, re.IGNORECASE)
        if commands:
            task.command_plan = [{"command": c.strip(), "purpose": task.goal} for c in commands[:5]]
        mod_m = re.search(r"module:\s*(\w+)", raw, re.I)
        if mod_m:
            task.module = mod_m.group(1).lower()
        return task

    def _parse_with_llm(self, task: ExecutorTask, result: ExecutorResult, state: SessionState) -> ExecutorResult:
        prompt = (
            f"Parse hasil eksekusi ini ke format EXECUTOR_RESULT.\n"
            f"Task: {task.goal}\n"
            f"Commands: {result.commands_used}\n"
            f"Raw output:\n{result.output_summary[:4000]}\n"
        )
        raw, _ = self._call(
            self.config.models.executor,
            self.config.models.executor_backup,
            self.system_prompt,
            [],
            prompt,
            role="executor",
            timeout=self.config.models.executor_timeout_seconds,
        )
        parsed = self._parse_executor_result(raw, result)
        return parsed

    def _parse_executor_result(self, raw: str, fallback: ExecutorResult) -> ExecutorResult:
        def extract(field: str) -> str:
            m = re.search(rf"-\s*{field}:\s*(.+)", raw, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        evidence = re.findall(r"^\s*-\s+(.+)$", extract("evidence") or raw, re.MULTILINE)[:20]
        observations = re.findall(r"^\s*-\s+(.+)$", extract("observations") or "", re.MULTILINE)[:10]
        status = extract("status") or fallback.status
        return ExecutorResult(
            task_id=fallback.task_id,
            status=status if status in {"success", "failed", "blocked", "needs_approval"} else fallback.status,
            permission_check=fallback.permission_check,
            commands_used=fallback.commands_used,
            output_summary=extract("output_summary") or fallback.output_summary,
            evidence=evidence or fallback.evidence,
            observations=observations or fallback.observations,
            risk=extract("risk") or fallback.risk,
            recommended_next_step=extract("recommended_next_step") or fallback.recommended_next_step,
            errors=extract("errors") or fallback.errors,
        )

    def format_result(self, result: ExecutorResult) -> str:
        lines = [
            "EXECUTOR_RESULT:",
            f"- Task ID: {result.task_id}",
            f"- Status: {result.status}",
            f"- Permission check: {json.dumps(result.permission_check, ensure_ascii=False)}",
            "- Commands used:",
        ]
        for cmd in result.commands_used:
            lines.append(f"  - {cmd}")
        lines.extend([
            f"- Output summary: {result.output_summary}",
            "- Evidence:",
        ])
        for e in result.evidence:
            lines.append(f"  - {e}")
        lines.extend([
            "- Observations:",
        ])
        for o in result.observations:
            lines.append(f"  - {o}")
        lines.extend([
            f"- Risk: {result.risk}",
            f"- Recommended next step: {result.recommended_next_step}",
            f"- Errors: {result.errors}",
        ])
        return "\n".join(lines)
