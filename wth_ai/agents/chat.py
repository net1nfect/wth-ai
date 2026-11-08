"""Natural conversation agent — chat mode like modern AI CLI."""

from __future__ import annotations

from typing import Any, Callable

from wth_ai.agents import BaseLLMAgent, load_prompt
from wth_ai.config import WTHConfig
from wth_ai.models import SessionState
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.skills.registry import SkillRegistry
from wth_ai.skills.router import SkillRouter
from wth_ai import veracity


class ChatAgent(BaseLLMAgent):
    """Handle casual conversation and capability questions.

    Two things this agent used to get wrong, both fixed here:

    1. It assembled its own bespoke context (session/target/permission + skill
       text) and ignored the project's learning loop entirely, even though
       `HuntMemoryLearning`, `ActionMemory` and the asset graph were already
       wired into the planner path. Chat is the surface the operator actually
       talks to, so the agent answered questions about its own engagement
       WITHOUT the experience it had accumulated. It now goes through
       `ContextBuilder`, the same assembler the supervisor uses.
    2. It advertised models by name in prose ("Planner Brain (Nemotron) /
       Executor Brain (Qwen Coder)") that had nothing to do with the configured
       provider, and its replies were never checked for absolutist claims.
       Self-description is now derived from live config, and every reply passes
       the `veracity` gate before it reaches the operator.
    """

    def __init__(
        self,
        config: WTHConfig,
        client: LLMProviderRouter,
        skills: SkillRegistry,
        *,
        context_builder: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(config, client)
        self.system_prompt = load_prompt(config, "chat_system.txt")
        self.skill_router = SkillRouter(skills)
        # A CALLABLE, not a ready object: ChatAgent is constructed before
        # AssetGraph/learning wiring finishes in WTHOrchestrator.__init__, so an
        # eagerly captured reference would pin chat to a half-built graph.
        self._context_builder = context_builder

    # ------------------------------------------------------------------ #
    # context
    # ------------------------------------------------------------------ #
    def _experience_context(self, state: SessionState, user_input: str) -> str:
        if self._context_builder is None:
            return ""
        try:
            builder = self._context_builder()
        except Exception:  # noqa: BLE001 - context is an enhancement, never fatal
            return ""
        if builder is None:
            return ""
        # Same sources the planner reads, so accumulated experience reaches the
        # conversation too instead of being trapped in the planning path.
        return builder.build(
            state,
            sources=["jobs", "learning", "action_memory", "engagement_graph", "skill_fusion"],
            char_budget=6000,
            user_input=user_input,
        )

    def _self_facts(self, state: SessionState) -> str:
        """Facts about this tool, read from live config — never hardcoded."""
        role_cfg = self.config.providers.get("planner")
        provider = getattr(role_cfg, "type", None) or "belum diset"
        model = getattr(role_cfg, "model", None) or self.config.models.planner
        return veracity.self_description_block(
            provider=provider,
            model=model,
            skills=len(self.skill_router.registry.skills),
            permission=state.permission_mode.value,
        )

    def _build_context(
        self, state: SessionState, user_input: str, active_skills: list[Any], skill_ctx: str
    ) -> str:
        parts = [
            f"Session: {state.session_id}",
            f"Permission: {state.permission_mode.value}",
            f"Target: {state.scope.target or 'belum diset'}",
            f"Environment: {state.scope.environment}",
            f"Authorized: {state.scope.authorized}",
            f"Phase: {state.current_phase.value}",
            f"Skills loaded: {len(self.skill_router.registry.skills)}",
            f"Active skills: {', '.join(s.id for s in active_skills)}",
            self._self_facts(state),
            veracity.evidence_discipline_block(),
            skill_ctx,
        ]
        experience = self._experience_context(state, user_input)
        if experience:
            parts.append("PENGALAMAN DARI ENGAGEMENT SEBELUMNYA:\n" + experience)
        return "\n".join(p for p in parts if p)

    # ------------------------------------------------------------------ #
    # run
    # ------------------------------------------------------------------ #
    def respond(self, user_input: str, state: SessionState) -> dict[str, Any]:
        active_skills = self.skill_router.route(user_input, limit=2)
        skill_ctx = self.skill_router.build_context(active_skills, max_chars=8000)

        context = self._build_context(state, user_input, active_skills, skill_ctx)
        prompt = f"{context}\n\nUser: {user_input}"
        raw, model = self._call(
            self.config.models.planner,
            self.config.models.planner_backup,
            self.system_prompt,
            state.conversation,
            prompt,
            role="planner",
        )

        # The gate runs on OUR output, not on the model's good intentions:
        # `prompts/chat_system.txt` already asked for hypothesis/evidence
        # separation and was routinely ignored, because nothing checked.
        guarded, violations = veracity.sanitize_claims(raw)
        result: dict[str, Any] = {
            "raw": guarded,
            "model": model,
            "skills_used": [s.id for s in active_skills],
            "mode": "chat",
        }
        if violations:
            result["raw"] = f"{guarded}\n\n{veracity.guard_note(violations)}"
            result["veracity_violations"] = [v.describe() for v in violations]
        return result

    def capabilities_summary(self, skills: SkillRegistry) -> str:
        """Capability text built from live state.

        The previous version hardcoded "Planner Brain (Nemotron) / Executor
        Brain (Qwen Coder)" plus a skill count that drifted from the registry,
        so the tool described itself incorrectly whenever the provider changed.
        """
        role_cfg = self.config.providers.get("planner")
        provider = getattr(role_cfg, "type", None) or "belum diset"
        model = getattr(role_cfg, "model", None) or self.config.models.planner
        total = len(skills.skills)
        return f"""**WTH-AI — Cybersecurity AI CLI (offensive-security focus)**

**Model yang BENAR-BENAR aktif sekarang:** `{provider}/{model}`
(setiap kali ditanya, sebut ini — bukan model lain)

**Yang bisa dilakukan:**
- Chat natural seperti AI CLI biasa, plus slash command (`/help`)
- Recon & pemetaan attack surface (registry 200+ tool, profile: budget/recon-only/default/high-quality/local)
- Normalisasi + asset graph + scoring prioritas target
- Validasi non-destruktif dengan gate scope + permission (read_only → full_lab)
- Verifikasi temuan: 7-question gate (tolak false positive) sebelum disebut temuan
- Report siap triase + diff antar sesi (`wth-ai recon diff`)

**Skills terdeteksi: {total}** (pakai `/skills` untuk lihat)

**Disiplin bukti (ditegakkan kode, bukan imbauan):**
- Klaim "terkonfirmasi" hanya dengan bukti request/response + 1 reproduksi + 1 kontrol pembanding
- Kalau belum lengkap, levelnya disebut eksplisit: kandidat / belum terbukti
- Hasil negatif dilaporkan sebagai hasil negatif

**Mulai:**
```
/scope set target=domain-anda.com authorized=yes environment=staging
/permission standard
/plan
/recon
```

Ketik apa saja — saya bisa chat normal juga."""
