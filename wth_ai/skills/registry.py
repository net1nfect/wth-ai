"""Load and index cybersecurity skills from OpenClaw and local bundles."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    id: str
    name: str
    description: str
    path: Path
    source: str
    tags: list[str] = field(default_factory=list)
    content_preview: str = ""

    def load_content(self, max_chars: int = 12000) -> str:
        skill_md = self.path / "SKILL.md"
        if not skill_md.exists():
            return ""
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars]


@dataclass
class SkillRegistry:
    """Index skills from multiple directories."""

    openclaw_path: Path = field(default_factory=lambda: Path.home() / ".openclaw/workspace/skills")
    local_path: Path = field(default_factory=lambda: Path("skills"))
    skills: dict[str, Skill] = field(default_factory=dict)

    def discover(self) -> int:
        self.skills.clear()
        count = 0
        sources = [("openclaw", self.openclaw_path), ("local", self.local_path)]
        # Skill otomatis hidup di $WTH_HOME/skills (ditulis `skills/autowrite.py`).
        # Tanpa dimasukkan ke sini, skill hasil self-improvement tidak pernah terbaca.
        try:
            from wth_ai.workspace import wth_home

            sources.append(("wth", wth_home() / "skills"))
        except Exception:  # noqa: BLE001
            pass
        for source, base in sources:
            if not base.exists():
                continue
            for entry in sorted(base.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.is_symlink() and not entry.exists():
                    continue
                skill = self._parse_skill(entry, source)
                if skill:
                    self.skills[skill.id] = skill
                    count += 1
                    continue
                # Satu level namespace (mis. `skills/auto/<slug>/SKILL.md`). Tanpa ini,
                # skill yang ditulis otomatis TIDAK PERNAH terbaca -- terukur di uji
                # end-to-end: file tertulis di disk, registry melaporkan 117 skill lama
                # dan nol skill auto.
                for nested in sorted(entry.iterdir()):
                    if not nested.is_dir() or (nested.is_symlink() and not nested.exists()):
                        continue
                    nested_skill = self._parse_skill(nested, f"{source}/{entry.name}")
                    if nested_skill:
                        self.skills[nested_skill.id] = nested_skill
                        count += 1
        return count

    def _parse_skill(self, path: Path, source: str) -> Skill | None:
        skill_md = path / "SKILL.md"
        if not skill_md.exists():
            return None
        raw = skill_md.read_text(encoding="utf-8", errors="replace")
        meta: dict[str, Any] = {}
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    meta = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    meta = {}
                body = parts[2]

        name = str(meta.get("name", path.name))
        desc = str(meta.get("description", ""))[:500]
        tags = self._extract_tags(name, desc, body)
        return Skill(
            id=path.name,
            name=name,
            description=desc,
            path=path,
            source=source,
            tags=tags,
            content_preview=body[:800].strip(),
        )

    def _extract_tags(self, name: str, desc: str, body: str) -> list[str]:
        text = f"{name} {desc} {body[:2000]}".lower()
        keywords = [
            "xss", "sqli", "ssrf", "xxe", "idor", "bola", "jwt", "oauth", "csrf",
            "recon", "api", "graphql", "ssti", "lfi", "rce", "auth", "cloud",
            "kubernetes", "android", "ios", "ctf", "pwn", "reverse", "crypto",
            "forensics", "business logic", "race", "upload", "smuggling", "waf",
            "llm", "prompt injection", "web3", "defi", "active directory",
        ]
        return [k for k in keywords if k in text]

    def list_skills(self) -> list[Skill]:
        return sorted(self.skills.values(), key=lambda s: s.id)

    def get(self, skill_id: str) -> Skill | None:
        return self.skills.get(skill_id)

    def search(self, query: str, limit: int = 10) -> list[Skill]:
        q = query.lower()
        scored: list[tuple[int, Skill]] = []
        for skill in self.skills.values():
            score = 0
            if q in skill.id.lower():
                score += 10
            if q in skill.name.lower():
                score += 8
            if q in skill.description.lower():
                score += 5
            for tag in skill.tags:
                if q in tag or tag in q:
                    score += 3
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]

    def format_catalog(self, category: str | None = None) -> str:
        skills = self.list_skills()
        if category:
            cat = category.lower()
            skills = [s for s in skills if cat in s.id or cat in s.tags or cat in s.description.lower()]

        lines = [
            f"WTH-AI Skills Catalog — {len(skills)} skills loaded",
            f"Sources: OpenClaw ({self.openclaw_path}) + local ({self.local_path})",
            "",
        ]
        by_prefix: dict[str, list[Skill]] = {}
        for s in skills:
            prefix = s.id.split("-")[0] if "-" in s.id else "general"
            by_prefix.setdefault(prefix, []).append(s)

        for prefix in sorted(by_prefix.keys()):
            lines.append(f"## {prefix.upper()} ({len(by_prefix[prefix])})")
            for s in by_prefix[prefix][:15]:
                lines.append(f"  • /skill {s.id} — {s.name}: {s.description[:80]}...")
            if len(by_prefix[prefix]) > 15:
                lines.append(f"  ... +{len(by_prefix[prefix]) - 15} more")
            lines.append("")
        return "\n".join(lines)

    def ensure_symlink(self) -> Path:
        """Link local skills/external → OpenClaw skills if available."""
        link = self.local_path / "external"
        link.parent.mkdir(parents=True, exist_ok=True)
        if self.openclaw_path.exists():
            if link.is_symlink() or link.exists():
                return link
            try:
                link.symlink_to(self.openclaw_path, target_is_directory=True)
            except OSError:
                pass
        return link
