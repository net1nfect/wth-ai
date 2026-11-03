"""Closed engagement loop v2 — graph-driven hunt with hypothesis coverage."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from wth_ai.ctf.packs import ctf_wordlists, suggest_challenge_type
from wth_ai.engagement.mode import EngagementMode, get_engagement_mode
from wth_ai.brain.chain_planner import ChainPlanner
from wth_ai.brain.hypothesis_engine import HypothesisEngine
from wth_ai.events import get_event_bus
from wth_ai.executor.evidence import EvidencePipeline
from wth_ai.loop.state_machine import LoopState, LoopStateMachine
from wth_ai.models import AttackSurface, ExecutorTask, FindingState

if TYPE_CHECKING:
    from wth_ai.orchestrator import WTHOrchestrator


class EngagementLoop:
    """Graph-driven hunt: recon → graph → hypotheses → chains → execute → verify."""

    def __init__(self, orchestrator: WTHOrchestrator) -> None:
        self.orch = orchestrator
        # WU-105: wire the process-wide global bus so invalid_state_transition
        # (WU-006) actually reaches a subscriber (AuditLogSink) -- previously
        # LoopStateMachine() was constructed with no bus at all here.
        self.sm = LoopStateMachine(bus=get_event_bus())
        self.evidence = EvidencePipeline()
        self.hypothesis_engine = HypothesisEngine()
        self.chain_planner = ChainPlanner()

    def run(self, goal: str, *, max_iterations: int = 5) -> str:
        orch = self.orch
        memory = orch.memory
        lines: list[str] = [f"# Hunt v2 — {orch.state.scope.target or 'no target'}", ""]

        if get_engagement_mode() == EngagementMode.CTF:
            fp = orch.state.metadata.get("fingerprint", {})
            ctype = suggest_challenge_type(fp if isinstance(fp, dict) else {})
            lines.append(f"## CTF mode — suggested type: {ctype}")
            lines.append(f"- Wordlist packs: {len(ctf_wordlists(ctype))}")

        if not orch.state.scope.target:
            return "Set scope first: `/target domain.com` atau `/scope set target=domain.com authorized=yes`"

        self.sm.transition(LoopState.RECON)
        force_recon = "force" in goal.lower() or not orch.asset_graph.coverage().get("nodes")
        if force_recon or not orch.state.metadata.get("last_recon"):
            orch.state.metadata.pop("last_recon", None)
            recon_result = orch._run_recon([])
            lines.append("## Phase: Recon")
            lines.append(recon_result[:2000])
            memory.sync_from_graph(orch.asset_graph)
        else:
            lines.append("## Phase: Recon (cached)")
            lines.append(f"- Graph nodes: {orch.asset_graph.coverage().get('nodes', 0)}")

        tech_stack = (orch.state.metadata.get("attack_surface_obj") or {}).get("tech_stack", {})
        tech_profile = orch.state.metadata.get("tech_profile", "generic_web")
        recon_meta = orch.state.metadata.get("last_recon", {})
        if recon_meta.get("adaptive_profile"):
            tech_profile = recon_meta["adaptive_profile"]
        pre_hypotheses = self.hypothesis_engine.generate(orch.asset_graph, tech_stack)
        chains = self.chain_planner.plan_from_hypotheses(pre_hypotheses, orch.state.scope.target or "")
        orch.state.metadata["pre_hypotheses"] = pre_hypotheses
        orch.state.metadata["exploit_chains"] = [c.to_dict() for c in chains]
        lines.append(f"- Hypotheses: {len(pre_hypotheses)} | Chains: {len(chains)}")

        for iteration in range(1, max_iterations + 1):
            memory.iteration = iteration
            memory.save()
            self.sm.transition(LoopState.PLANNING)
            cov_before = orch.asset_graph.coverage().get("coverage_pct", 0)

            plan_input = (
                f"{goal}\n\nITERATION {iteration}\n"
                f"Graph coverage: {cov_before}%\n{memory.context_block()}"
            )
            nudge = orch.learning.engagement_nudge(tech_profile)
            if nudge:
                plan_input += f"\n{nudge}\n"
            plan_result = orch.planner.plan(
                plan_input, orch.state,
                resume_context=memory.context_block(),
                skill_context=orch.skill_router.fuse(
                    goal,
                    tech_profile=orch.state.metadata.get("tech_profile", "generic_web"),
                    tech_stack=tech_stack,
                ).to_context(),
                graph_context=orch.asset_graph.export_for_planner(),
                pre_hypotheses=pre_hypotheses,
                chain_suggestions=[c.to_dict() for c in chains],
            )
            orch.state.planner_summary = plan_result.get("planner_summary", "")
            for hyp in plan_result.get("hypotheses", []):
                orch.findings_db.add_hypothesis(hyp)
            tasks = plan_result.get("tasks", [])
            if not tasks and chains:
                tasks = self.chain_planner.chains_to_tasks(chains)
            if not tasks:
                lines.append(f"\n## Iteration {iteration}: no tasks — stopping")
                break
            orch.state.task_queue = [orch._task_to_dict(t) for t in tasks]
            lines.append(
                f"\n## Iteration {iteration}: {len(tasks)} tasks "
                f"(coverage {cov_before}% → target higher)"
            )

            self.sm.transition(LoopState.EXECUTING)
            for task_dict in orch.state.task_queue[:3]:
                task = ExecutorTask.from_dict(task_dict)
                result = orch.executor.execute_module(task, orch.state)
                pairs = orch.state.metadata.pop("request_pairs", [])
                if pairs:
                    orch.evidence_vault.store_from_executor(task.id, pairs, task.evidence_schema)
                memory.update_from_executor(result.to_dict())
                orch.state.completed_tasks.append(result.to_dict())
                lines.append(f"- Task {task.id} ({task.evidence_schema.get('type', task.module)}): {result.status}")

                # WU-006: verifikasi per-task tetap terjadi di sini (logic tidak
                # berubah) -- tapi transisi state machine ke VERIFYING dipindah
                # ke SEKALI setelah seluruh batch task_queue[:3] selesai (lihat
                # bawah), bukan per-task. EXECUTING/VERIFYING merepresentasikan
                # fase iterasi ini secara keseluruhan (bisa >1 task per batch),
                # bukan mikro-state per-task -- TRANSITIONS tidak mendefinisikan
                # VERIFYING->EXECUTING untuk task berikutnya dalam batch yang sama.
                candidates = self.evidence.to_findings_candidates(
                    self.evidence.from_executor_result(result)
                )
                for cand in candidates:
                    orch.findings_db.add_finding(cand)
                    finding = orch.findings_db.findings.get(cand["id"])
                    if not finding:
                        continue
                    schema = task.evidence_schema or {}
                    if schema:
                        ok, missing = orch.evidence_vault.validate_schema(task.id, schema)
                        if not ok:
                            lines.append(f"  ! Evidence gate: missing {missing}")
                    import os
                    if orch.client.available():
                        v = orch.verifier.verify(finding, orch.state, evidence_vault=orch.evidence_vault)
                    elif os.environ.get("WTH_TEST_MODE") == "1":
                        v = orch.verifier.verify_offline(finding, orch.evidence_vault)
                    else:
                        lines.append("LLM tidak tersedia untuk verifikasi.")
                        continue
                    verdict = v.get("verdict", "needs_more_evidence")
                    replan = v.get("replan_hint", "")
                    if verdict == "verified":
                        orch.findings_db.update_finding_state(finding.id, FindingState.VERIFIED)
                        lines.append(f"  ✓ VERIFIED: {finding.title}")
                    elif verdict == "false_positive":
                        orch.findings_db.update_finding_state(finding.id, FindingState.FALSE_POSITIVE)
                        memory.add_rejection(finding.id, replan or "false positive")
                        lines.append(f"  ✗ FALSE POSITIVE: {finding.title}")
                    else:
                        memory.add_evidence_gap(finding.id, replan or "more evidence needed")
                        lines.append(f"  ? NEEDS EVIDENCE: {finding.title}")

            self.sm.transition(LoopState.VERIFYING)
            cov = orch.asset_graph.coverage()
            lines.append(f"- Graph coverage: {cov.get('coverage_pct', 0)}%")
            verified = [f for f in orch.findings_db.findings.values() if f.state == FindingState.VERIFIED]
            if any(f.severity in {"critical", "high"} for f in verified):
                lines.append("\nHigh/critical verified — moving to report")
                break
            self.sm.transition(LoopState.REPLANNING)
            orch.state.task_queue = []

        self.sm.transition(LoopState.REPORTING)
        report = orch._report()
        lines.append("\n## Report")
        lines.append(report[:3000])
        orch._sync_state_from_db()
        orch._persist()
        return "\n".join(lines)
