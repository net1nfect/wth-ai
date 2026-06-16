"""Index skills from multiple directories for fusion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wth_ai.skills.registry import Skill, SkillRegistry


@dataclass
class SkillIndex:
    """Searchable index over all discovered skills."""

    skills: dict[str, Skill] = field(default_factory=dict)
    tag_index: dict[str, list[str]] = field(default_factory=dict)
    vuln_index: dict[str, list[str]] = field(default_factory=dict)

    VULN_KEYWORDS = {
        "xss": ["xss", "cross-site"],
        "sqli": ["sqli", "sql injection", "sql-injection"],
        "ssrf": ["ssrf", "server-side request"],
        "idor": ["idor", "bola", "object authorization"],
        "auth": ["auth", "jwt", "oauth", "saml"],
        "api": ["api", "graphql", "rest"],
        "ssti": ["ssti", "template injection"],
        "upload": ["upload", "file upload"],
        "llm": ["llm", "prompt injection", "ai"],
        "recon": ["recon", "enumeration"],
    }

    @classmethod
    def from_registry(cls, registry: SkillRegistry, *, extra_paths: list[Path] | None = None) -> SkillIndex:
        idx = cls()
        registry.discover()
        idx.skills.update(registry.skills)

        if extra_paths:
            for base in extra_paths:
                if not base.exists():
                    continue
                for entry in sorted(base.iterdir()):
                    if not entry.is_dir():
                        continue
                    skill_md = entry / "SKILL.md"
                    if not skill_md.exists() or entry.name in idx.skills:
                        continue
                    raw = skill_md.read_text(encoding="utf-8", errors="replace")
                    meta: dict[str, Any] = {}
                    if raw.startswith("---"):
                        parts = raw.split("---", 2)
                        if len(parts) >= 3:
                            try:
                                meta = yaml.safe_load(parts[1]) or {}
                            except yaml.YAMLError:
                                meta = {}
                    idx.skills[entry.name] = Skill(
                        id=entry.name,
                        name=str(meta.get("name", entry.name)),
                        description=str(meta.get("description", ""))[:500],
                        path=entry,
                        source="codex",
                        tags=[],
                    )

        for sid, skill in idx.skills.items():
            blob = f"{skill.name} {skill.description} {skill.id}".lower()
            for tag in skill.tags:
                idx.tag_index.setdefault(tag, []).append(sid)
            for vuln, kws in cls.VULN_KEYWORDS.items():
                if any(k in blob for k in kws):
                    idx.vuln_index.setdefault(vuln, []).append(sid)

        return idx

    def search(self, query: str, *, vuln_class: str | None = None, limit: int = 10) -> list[Skill]:
        q = query.lower()
        candidates: list[tuple[int, Skill]] = []

        pool = list(self.skills.values())
        if vuln_class and vuln_class in self.vuln_index:
            pool = [self.skills[sid] for sid in self.vuln_index[vuln_class] if sid in self.skills]

        for skill in pool:
            score = 0
            blob = f"{skill.id} {skill.name} {skill.description}".lower()
            for word in re.findall(r"\w+", q):
                if word in blob:
                    score += 2
            if skill.id in q:
                score += 5
            if score > 0:
                candidates.append((score, skill))

        candidates.sort(key=lambda x: -x[0])
        return [s for _, s in candidates[:limit]]

    def extract_sections(self, skill: Skill, keywords: list[str], max_chars: int = 2000) -> str:
        """Extract relevant sections (bypass tables, payloads) from skill content."""
        content = skill.load_content(20000)
        if not content:
            return ""
        sections: list[str] = []
        current: list[str] = []
        for line in content.splitlines():
            lower = line.lower()
            if line.startswith("#") or line.startswith("##"):
                if current:
                    block = "\n".join(current)
                    if any(k in block.lower() for k in keywords):
                        sections.append(block[:max_chars])
                    current = []
            current.append(line)
        if current:
            block = "\n".join(current)
            if any(k in block.lower() for k in keywords) or not keywords:
                sections.append(block[:max_chars])
        return "\n\n".join(sections[:3])
