"""`ContextBuilder` — unified context-block assembly (WU-111, ADR-004).

ADR-004 rejects one monolithic `MemoryService` hiding all 5 memory
mechanisms (`ActionMemory`, `EngagementMemory`, `SessionFtsIndex`,
`HuntMemoryLearning`, `MdContext`) behind a single API -- lifecycles genuinely
differ (session-ephemeral vs cross-session-statistical vs cross-session-
lexical vs human-authored). Instead: each mechanism stays independent, but
exposes/consumes a thin shared contract here (`SOURCE_REGISTRY` names ->
resolver), so both context-building call sites can select a DIFFERENT subset
of the SAME sources rather than each writing its own bespoke assembly logic
that silently drifts (the actual bug found during Fase B research: two call
sites invoked with equal frequency, unequal quality, from being built at
different times).

Scope decision (found during implementation, not in the original ADR/
INTERFACES.md sketch): only `SupervisorAgent._build_context_block()` is
migrated to use this class in WU-111. `WTHOrchestrator._assess_loop()` is
NOT migrated -- it doesn't build a single context STRING at all; it passes
5+ separate named pieces (`resume_context`, `skill_context`,
`learning_context`, `graph_context`, `pre_hypotheses`, `chain_suggestions`)
as distinct keyword arguments directly into `PlannerAgent.plan()`, a
structurally different calling convention. Unifying that would require also
changing `PlannerAgent.plan()`'s signature and internal prompt assembly --
a materially bigger, riskier change than this WU's scope (deferred, not
silently dropped).
"""

from __future__ import annotations

from typing import Any

SOURCE_NAMES: tuple[str, ...] = (
    "action_memory", "md_context", "engagement_graph", "learning", "skill_fusion", "fts_recall",
    "jobs",
)


class ContextBuilder:
    """Dependencies are injected at construction (each call site wires only
    what it actually has available); a source whose dependency is `None`
    contributes an empty string rather than raising."""

    def __init__(
        self,
        *,
        action_memory: Any = None,
        asset_graph: Any = None,
        learning: Any = None,
        skill_router: Any = None,
        jobs: Any = None,
    ) -> None:
        self.action_memory = action_memory
        self.asset_graph = asset_graph
        self.learning = learning
        self.skill_router = skill_router
        # Callable returning the recall text for the CURRENT state's target.
        # A callable, not a string, because the block depends on which target is
        # in scope at the moment the context is built.
        self.jobs = jobs

    def build(
        self, state: Any, *, sources: list[str], char_budget: int = 0, user_input: str = "",
    ) -> str:
        """Session-summary lines are ALWAYS included (unconditional preamble
        -- matches `SupervisorAgent._build_context_block()`'s pre-existing
        behavior exactly, migrated here rather than changed), followed by
        the selected `sources` in the order given. `char_budget<=0` means
        unbounded (no truncation)."""
        parts = [self._session_summary(state)]
        for name in sources:
            piece = self._resolve(name, state, user_input=user_input)
            if piece:
                parts.append(piece)
        text = "\n".join(p for p in parts if p)
        return text[:char_budget] if char_budget > 0 else text

    def _session_summary(self, state: Any) -> str:
        cov = self.asset_graph.coverage() if self.asset_graph else {}
        return "\n".join([
            f"Session: {state.session_id}",
            f"Target: {state.scope.target or 'unset'}",
            f"Authorized: {state.scope.authorized}",
            f"Permission: {state.permission_mode.value}",
            f"Phase: {state.current_phase.value}",
            f"Graph nodes: {cov.get('nodes', 0)} coverage: {cov.get('coverage_pct', 0)}%",
            f"Queued tasks: {len(state.task_queue)}",
        ])

    def _resolve(self, name: str, state: Any, *, user_input: str) -> str:
        if name == "action_memory":
            return self.action_memory.context_block() if self.action_memory else ""
        if name == "md_context":
            from wth_ai.memory.md_context import context_block as md_block
            return md_block()
        if name == "engagement_graph":
            if not self.asset_graph or not self.asset_graph.nodes:
                return ""
            return self.asset_graph.export_for_planner()
        if name == "learning":
            if not self.learning:
                return ""
            tech_profile = state.metadata.get("tech_profile", "generic_web")
            return self.learning.build_planner_context(tech_profile)
        if name == "skill_fusion":
            if not self.skill_router or not user_input:
                return ""
            return self.skill_router.fuse(user_input).to_context()
        if name == "fts_recall":
            # ADR-004: SessionFtsIndex is explicitly "agent-pull-only lewat
            # tool session_search" -- there's no natural auto-query to
            # search for at context-assembly time, so this source
            # deliberately contributes nothing (not a missing feature).
            return ""
        if name == "jobs":
            # The bridge the chat surface lacked: recon results live in the job
            # store, chat context comes from the session asset graph, so without
            # this the agent answered "belum ada recon di sesi ini" while
            # completed jobs for the same target sat on disk.
            if self.jobs is None:
                return ""
            target = getattr(getattr(state, "scope", None), "target", "") or ""
            try:
                return self.jobs(target) or ""
            except Exception:  # noqa: BLE001 - recall must never break a turn
                return ""
        return ""
