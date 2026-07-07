"""Planner / Recon Brain — multi-agent Hunter → Critic → Feasibility."""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

from wth_ai.agents import BaseLLMAgent, build_messages, load_prompt, parse_blocks
from wth_ai.config import WTHConfig
from wth_ai.models import (
    AssessmentPhase,
    ExecutorTask,
    KilledHypothesis,
    PermissionMode,
    PlanHypothesis,
    PlanOutput,
    SessionState,
)
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.planner.profiles import get_profile
from wth_ai.skills.fusion import SkillFusionEngine
from wth_ai.skills.router import SkillRouter
from wth_ai.agents.tool_loop import AgentToolLoop
from wth_ai.tools.registry import ToolRegistry


CRITIC_SYSTEM = """You are the Critic agent in WTH-AI. Apply the 7-Question Gate to each hypothesis:
1. Is it in scope? 2. Is there a real attack surface? 3. Is impact demonstrable?
4. Is it reproducible? 5. Is it not self-inflicted? 6. Is it not theoretical only?
7. Would a triager accept this?

For each weak hypothesis, output:
KILLED_HYPOTHESIS:
- id: HYP-xxx
- title: ...
- reason: ...

Keep strong hypotheses. Be ruthless — kill hallucinated or low-value items."""


FEASIBILITY_SYSTEM = """You are the Feasibility agent. Review EXECUTOR_TASK blocks.
Remove or mark tasks whose allowed_tools are not available.
Output FEASIBLE_TASK blocks only — same format as EXECUTOR_TASK.
Add evidence_schema field: type, proof, severity_min."""


class PlannerAgent(BaseLLMAgent):
    """Strategic planning via Hunter → Critic → Feasibility pipeline."""

    def __init__(
        self,
        config: WTHConfig,
        client: LLMProviderRouter,
        skill_router: SkillRouter | SkillFusionEngine | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        super().__init__(config, client)
        v5 = load_prompt(config, "planner_system_v5.txt")
        self.system_prompt = v5 or load_prompt(config, "planner_system.txt")
        self.skill_router = skill_router
        self.tool_registry = tool_registry or ToolRegistry()
        self._terminal = None
        self._graph = None
        self._vault = None

    def set_terminal(self, terminal, graph, vault) -> None:
        self._terminal = terminal
        self._graph = graph
        self._vault = vault

    def plan(
        self,
        user_input: str,
        state: SessionState,
        *,
        resume_context: str = "",
        skill_context: str = "",
        learning_context: str = "",
        graph_context: str = "",
        pre_hypotheses: list[dict[str, Any]] | None = None,
        chain_suggestions: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        context = self._build_context(
            state, resume_context, skill_context, learning_context, graph_context,
            pre_hypotheses, chain_suggestions,
        )
        prompt = f"{context}\n\nUSER REQUEST:\n{user_input}"

        raw = ""
        model_used = "offline"
        parsed_json: dict[str, Any] | None = None

        if self.client.available() and not on_token:
            try:
                messages = build_messages(self.system_prompt, state.conversation, prompt)
                parsed_json, model_used = self.client.chat_json_for_role(
                    "planner",
                    messages,
                    timeout=self.config.models.planner_timeout_seconds,
                )
                raw = json.dumps(parsed_json, ensure_ascii=False, indent=2)
            except Exception:
                parsed_json = None

        if parsed_json is None:
            raw, model_used = self._call(
                self.config.models.planner,
                self.config.models.planner_backup,
                self.system_prompt,
                state.conversation,
                prompt,
                timeout=self.config.models.planner_timeout_seconds,
                on_token=on_token,
            )

        killed: list[KilledHypothesis] = []
        tasks: list[ExecutorTask] = []
        hypotheses: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        chains: list[dict[str, Any]] = chain_suggestions or []
        confidence = 0.2
        planner_summary = ""

        if parsed_json:
            killed = [
                KilledHypothesis(id=k.get("id", ""), title=k.get("title", ""), reason=k.get("reason", ""))
                for k in parsed_json.get("killed", [])
            ]
            tasks = self._tasks_from_json(parsed_json.get("tasks", []))
            hypotheses = self._hypotheses_from_json(parsed_json.get("hypotheses", []))
            candidates = parsed_json.get("candidates", [])
            chains = parsed_json.get("chains", chains)
            confidence = float(parsed_json.get("confidence", 0.5))
            planner_summary = parsed_json.get("planner_summary", "")
        elif self.client.available():
            critic_raw, _ = self._call(
                self.config.models.planner,
                self.config.models.planner_backup,
                CRITIC_SYSTEM,
                [],
                f"Review these hypotheses and tasks:\n\n{raw[:12000]}",
                timeout=60,
            )
            killed = self._extract_killed(critic_raw)
            killed_ids = {k.id for k in killed}
            tools_avail = self.tool_registry.installed_tools()[:80]
            feasible_raw, _ = self._call(
                self.config.models.planner_backup,
                self.config.models.planner,
                FEASIBILITY_SYSTEM,
                [],
                f"Installed tools: {', '.join(tools_avail)}\n\nTasks to validate:\n{raw[:10000]}",
                timeout=60,
            )
            if not parse_blocks(feasible_raw, "EXECUTOR_TASK") and not parse_blocks(feasible_raw, "FEASIBLE_TASK"):
                feasible_raw = raw
            tasks = self._extract_tasks(feasible_raw)
            if killed_ids:
                tasks = [t for t in tasks if not any(k in t.goal.lower() for k in ("theoretical", "unlikely"))]
            hypotheses = self._extract_hypotheses(raw)
            if killed_ids:
                hypotheses = [h for h in hypotheses if h.get("id") not in killed_ids]
            candidates = self._extract_candidates(raw)
            confidence = self._score_confidence(tasks, killed)
            planner_summary = self._extract_planner_summary(raw)
        else:
            tasks = self._extract_tasks(raw)
            hypotheses = self._extract_hypotheses(raw)
            confidence = self._score_confidence(tasks, killed)
            planner_summary = self._extract_planner_summary(raw)

        if pre_hypotheses:
            seen = {h.get("title") for h in hypotheses}
            for ph in pre_hypotheses:
                if ph.get("title") not in seen:
                    hypotheses.append(ph)

        profile_name = state.metadata.get("tech_profile", "generic_web")
        recon_meta = state.metadata.get("last_recon", {})
        if recon_meta.get("adaptive_profile"):
            profile_name = recon_meta["adaptive_profile"]

        probe_notes = self._probe_phase(hypotheses[:3], state)
        if probe_notes:
            planner_summary = (planner_summary or "") + f"\n\nPROBE_PHASE:\n{probe_notes}"
            state.metadata.setdefault("probe_transcripts", []).extend(
                re.findall(r"\[TX:[^\]]+\]", probe_notes)
            )

        plan_output = PlanOutput(
            hypotheses=[
                PlanHypothesis(
                    id=h.get("id", ""),
                    title=h.get("title", ""),
                    component=h.get("component", ""),
                    assumption=h.get("assumption", ""),
                    why_suspicious=h.get("why_suspicious", ""),
                    evidence_needed=h.get("evidence_needed", ""),
                    safe_test=h.get("safe_test", ""),
                    impact_if_true=h.get("impact_if_true", ""),
                    priority=int(h.get("priority", 5) or 5),
                )
                for h in hypotheses
            ],
            tasks=[self._task_to_dict(t) for t in tasks],
            chains=chains,
            killed=killed,
            candidates=candidates,
            confidence=confidence,
            planner_summary=planner_summary or self._extract_planner_summary(raw),
            raw=raw,
            model=model_used,
            tech_profile=profile_name,
        )

        return {
            "raw": raw,
            "model": model_used,
            "tasks": tasks,
            "hypotheses": hypotheses,
            "candidates": candidates,
            "killed": [k.model_dump() for k in killed],
            "chains": chains,
            "confidence": plan_output.confidence,
            "planner_summary": plan_output.planner_summary,
            "plan_output": plan_output,
        }

    def _probe_phase(self, hypotheses: list[dict[str, Any]], state: SessionState) -> str:
        """Optional live http_probe on top hypotheses via tool loop."""
        if not self._terminal or not self.client.available() or not hypotheses:
            return ""
        loop = AgentToolLoop(
            self.client, self._terminal, graph=self._graph, vault=self._vault, max_iters=4,
        )
        targets = [h.get("component", "") for h in hypotheses if h.get("component", "").startswith("http")][:3]
        if not targets:
            return ""
        prompt = (
            "Probe these URLs with http_probe ONLY. Report facts with [TX:...] citations. "
            "Do NOT claim verified vulns.\n" + "\n".join(f"- {u}" for u in targets)
        )
        result = loop.run(
            self.config.models.planner, self.system_prompt, prompt,
            actor="planner", backup=self.config.models.planner_backup, timeout=60,
        )
        return result.get("content", "")[:3000]

    def _build_context(
        self,
        state: SessionState,
        resume_context: str,
        skill_context: str = "",
        learning_context: str = "",
        graph_context: str = "",
        pre_hypotheses: list[dict[str, Any]] | None = None,
        chain_suggestions: list[dict[str, Any]] | None = None,
    ) -> str:
        recon_meta = state.metadata.get("last_recon", {})
        recon_block = ""
        if recon_meta:
            recon_block = (
                f"LAST RECON SUMMARY: {json.dumps(recon_meta, ensure_ascii=False)}\n"
                f"RECON ARTIFACTS: {state.metadata.get('recon_artifacts', '')}"
            )
        pre_findings = state.metadata.get("pre_findings", [])
        if pre_findings:
            recon_block += f"\nPRE_FINDINGS (diff brain): {json.dumps(pre_findings[:10], ensure_ascii=False)}"

        surface_sample = state.attack_surface[:40]
        profile_name = state.metadata.get("tech_profile", "generic_web")
        if recon_meta and recon_meta.get("adaptive_profile"):
            profile_name = recon_meta["adaptive_profile"]
        elif recon_meta and recon_meta.get("api_endpoints", 0) > 5:
            profile_name = "api"
        profile = get_profile(profile_name)
        parts = [
            resume_context,
            learning_context,
            skill_context,
            graph_context,
            profile.extra_context(None),
            recon_block,
        ]
        if pre_hypotheses:
            parts.append(f"PRE-HYPOTHESES (rule engine): {json.dumps(pre_hypotheses[:15], ensure_ascii=False)}")
        if chain_suggestions:
            parts.append(f"CHAIN SUGGESTIONS: {json.dumps(chain_suggestions[:8], ensure_ascii=False)}")
        parts.extend([
            f"PERMISSION MODE: {state.permission_mode.value}",
            f"PHASE: {state.current_phase.value}",
            f"SCOPE: {json.dumps(state.scope.to_dict(), ensure_ascii=False)}",
            f"KNOWN ASSETS: {state.known_assets[:50]}",
            f"ATTACK SURFACE ({len(state.attack_surface)} items, sample): {surface_sample}",
            f"COMPLETED TASKS: {len(state.completed_tasks)}",
            f"PENDING TASKS: {len(state.task_queue)}",
        ])
        if not state.attack_surface and not recon_meta:
            parts.append(
                "RECON STATUS: belum dijalankan — sarankan user jalankan /recon sebelum planning mendalam"
            )
        if state.completed_tasks:
            last = state.completed_tasks[-1]
            parts.append(f"LAST EXECUTOR RESULT: {json.dumps(last, ensure_ascii=False)[:2000]}")
        return "\n".join(p for p in parts if p)

    def _tasks_from_json(self, items: list[dict[str, Any]]) -> list[ExecutorTask]:
        tasks: list[ExecutorTask] = []
        for block in items:
            perm = block.get("permission_required", "read_only")
            try:
                perm_mode = PermissionMode(perm)
            except ValueError:
                perm_mode = PermissionMode.READ_ONLY_RECON
            schema = block.get("evidence_schema") or {}
            if isinstance(schema, str):
                schema = {"type": schema, "proof": "manual"}
            tasks.append(ExecutorTask(
                id=block.get("id", f"TASK-{uuid4().hex[:8]}"),
                goal=block.get("goal", ""),
                target=block.get("target", ""),
                permission_required=perm_mode,
                allowed_tools=block.get("allowed_tools", []),
                evidence_schema=schema or {"type": "general", "proof": "output_diff"},
                module=block.get("module", "dynamic").lower(),
            ))
        return tasks

    def _hypotheses_from_json(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**h, "id": h.get("id", f"HYP-{uuid4().hex[:8]}")} for h in items]

    def _extract_killed(self, raw: str) -> list[KilledHypothesis]:
        blocks = parse_blocks(raw, "KILLED_HYPOTHESIS")
        return [
            KilledHypothesis(
                id=b.get("id", ""),
                title=b.get("title", ""),
                reason=b.get("reason", ""),
            )
            for b in blocks
        ]

    def _score_confidence(self, tasks: list[ExecutorTask], killed: list[KilledHypothesis]) -> float:
        if not tasks:
            return 0.2
        base = min(0.9, 0.4 + len(tasks) * 0.1)
        penalty = min(0.3, len(killed) * 0.05)
        return round(max(0.1, base - penalty), 2)

    def _extract_tasks(self, raw: str) -> list[ExecutorTask]:
        blocks = parse_blocks(raw, "EXECUTOR_TASK") or parse_blocks(raw, "FEASIBLE_TASK")
        tasks = []
        for block in blocks:
            perm = block.get("permission_required", "read_only")
            try:
                perm_mode = PermissionMode(perm)
            except ValueError:
                perm_mode = PermissionMode.READ_ONLY_RECON
            command_plan = []
            if "command_plan" in block:
                for line in block["command_plan"].split("\n"):
                    if "command:" in line.lower():
                        cmd = line.split(":", 1)[-1].strip()
                        if cmd:
                            command_plan.append({"command": cmd, "purpose": block.get("goal", "")})
            evidence_schema: dict[str, Any] = {}
            if block.get("evidence_schema"):
                try:
                    evidence_schema = json.loads(block["evidence_schema"])
                except json.JSONDecodeError:
                    evidence_schema = {"type": block["evidence_schema"], "proof": "manual"}
            allowed = [t.strip() for t in block.get("allowed_tools", "").split(",") if t.strip()]
            if allowed:
                allowed = [t for t in allowed if self.tool_registry.is_installed(t) or not self.tool_registry.tools.get(t)]

            tasks.append(ExecutorTask(
                id=block.get("id", f"TASK-{uuid4().hex[:8]}"),
                goal=block.get("goal", ""),
                target=block.get("target", ""),
                permission_required=perm_mode,
                allowed_tools=allowed,
                forbidden_actions=[t.strip() for t in block.get("forbidden_actions", "").split(",") if t.strip()],
                command_plan=command_plan,
                expected_evidence=[e.strip() for e in block.get("expected_evidence", "").split(",") if e.strip()],
                evidence_schema=evidence_schema or {"type": "general", "proof": "output_diff", "severity_min": "info"},
                stop_condition=block.get("stop_condition", ""),
                success_criteria=block.get("success_criteria", ""),
                approval_needed="approval" in block.get("approval_needed", "").lower() if block.get("approval_needed") else False,
                module=block.get("module", "dynamic").lower(),
            ))
        return tasks

    def _task_to_dict(self, task: ExecutorTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "goal": task.goal,
            "target": task.target,
            "permission_required": task.permission_required.value,
            "allowed_tools": task.allowed_tools,
            "evidence_schema": task.evidence_schema,
            "module": task.module,
        }

    def _extract_hypotheses(self, raw: str) -> list[dict[str, Any]]:
        blocks = parse_blocks(raw, "HYPOTHESIS")
        return [{**b, "id": b.get("id", f"HYP-{uuid4().hex[:8]}")} for b in blocks]

    def _extract_candidates(self, raw: str) -> list[dict[str, Any]]:
        blocks = parse_blocks(raw, "FINDING_CANDIDATE")
        return [{**b, "id": b.get("id", f"FIND-{uuid4().hex[:8]}"), "state": "candidate"} for b in blocks]

    def _extract_planner_summary(self, raw: str) -> str:
        match = re.search(r"PLANNER_STATE:(.*?)(?=\nEXECUTOR_TASK:|\nHYPOTHESIS:|\Z)", raw, re.DOTALL | re.IGNORECASE)
        return match.group(0).strip() if match else raw[:2000]

    def infer_phase(self, user_input: str) -> AssessmentPhase:
        cmd = user_input.strip().lower()
        mapping = {
            "/recon": AssessmentPhase.RECON,
            "/static": AssessmentPhase.STATIC,
            "/dynamic": AssessmentPhase.DYNAMIC,
            "/hypotheses": AssessmentPhase.HYPOTHESIS,
            "/verify": AssessmentPhase.VERIFY,
            "/report": AssessmentPhase.REPORT,
            "/stop": AssessmentPhase.STOPPED,
            "/plan": AssessmentPhase.INTAKE,
        }
        for prefix, phase in mapping.items():
            if cmd.startswith(prefix):
                return phase
        return AssessmentPhase.RECON
