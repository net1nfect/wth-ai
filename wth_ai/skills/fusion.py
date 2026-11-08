"""Skill Fusion Engine — merge 117+ skills into curated composite playbooks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.models import tech_stack_blob
from wth_ai.skills.indexer import SkillIndex
from wth_ai.skills.registry import Skill, SkillRegistry
from wth_ai.skills.router import INTENT_SKILL_MAP, SkillRouter


@dataclass
class CompositePlaybook:
    """Curated skill fusion output for planner context."""

    skill_ids: list[str] = field(default_factory=list)
    vuln_classes: list[str] = field(default_factory=list)
    tech_profile: str = "generic_web"
    content: str = ""
    char_count: int = 0

    def to_context(self) -> str:
        if not self.content:
            return ""
        header = (
            f"=== COMPOSITE PLAYBOOK ({', '.join(self.skill_ids[:5])}) ===\n"
            f"Vuln focus: {', '.join(self.vuln_classes) or 'general'}\n"
            f"Tech profile: {self.tech_profile}\n"
        )
        return header + self.content + "\n=== END PLAYBOOK ==="


class SkillFusionEngine:
    """Dynamic playbook builder — replaces raw 3-skill injection."""

    SECTION_KEYWORDS = [
        "bypass", "payload", "grep", "pattern", "technique", "detection",
        "exploit", "proof", "poc", "table", "checklist",
    ]

    def __init__(self, registry: SkillRegistry, config: WTHConfig | None = None) -> None:
        self.registry = registry
        self.config = config or WTHConfig.load()
        self.router = SkillRouter(registry)
        extra = []
        if self.config.skills.codex_path.exists():
            extra.append(self.config.skills.codex_path)
        self.index = SkillIndex.from_registry(registry, extra_paths=extra)
        self.max_chars = self.config.skills.fusion_max_chars

    def detect_vuln_classes(self, user_input: str, tech_stack: dict[str, str] | None = None) -> list[str]:
        text = user_input.lower()
        found: list[str] = []
        for intent in INTENT_SKILL_MAP:
            if intent in text:
                found.append(intent)
        tech_blob = tech_stack_blob(tech_stack)
        if "laravel" in tech_blob or "php" in tech_blob:
            found.append("idor")
        if "api" in tech_blob or "graphql" in tech_blob:
            found.append("api")
        return list(dict.fromkeys(found))[:5]

    def fuse(
        self,
        user_input: str,
        *,
        tech_profile: str = "generic_web",
        tech_stack: dict[str, str] | None = None,
        limit_skills: int = 6,
    ) -> CompositePlaybook:
        vuln_classes = self.detect_vuln_classes(user_input, tech_stack)
        selected: list[Skill] = []
        seen: set[str] = set()

        for vc in vuln_classes:
            for skill in self.index.search(user_input, vuln_class=vc, limit=2):
                if skill.id not in seen:
                    selected.append(skill)
                    seen.add(skill.id)

        for skill in self.router.route(user_input, limit=3):
            if skill.id not in seen:
                selected.append(skill)
                seen.add(skill.id)

        if len(selected) < limit_skills:
            for skill in self.index.search(user_input, limit=limit_skills):
                if skill.id not in seen:
                    selected.append(skill)
                    seen.add(skill.id)
                if len(selected) >= limit_skills:
                    break

        parts: list[str] = []
        budget = self.max_chars
        keywords = self.SECTION_KEYWORDS + vuln_classes + [tech_profile]

        for skill in selected[:limit_skills]:
            section = self.index.extract_sections(skill, keywords, max_chars=min(2500, budget))
            if not section:
                section = skill.load_content(min(1500, budget))
            block = f"\n--- {skill.id} ---\n{section}\n"
            if len(block) > budget:
                block = block[:budget]
            parts.append(block)
            budget -= len(block)
            if budget <= 0:
                break

        content = "\n".join(parts)
        return CompositePlaybook(
            skill_ids=[s.id for s in selected[:limit_skills]],
            vuln_classes=vuln_classes,
            tech_profile=tech_profile,
            content=content,
            char_count=len(content),
        )

    def route(self, user_input: str, limit: int = 3) -> list[Skill]:
        """Backward-compatible skill routing."""
        return self.router.route(user_input, limit=limit)

    def build_context(self, user_input: str, **kwargs: Any) -> str:
        return self.fuse(user_input, **kwargs).to_context()

    def detect_intent(self, user_input: str, session_state: Any | None = None) -> str:
        return self.router.detect_intent(user_input, session_state=session_state)
