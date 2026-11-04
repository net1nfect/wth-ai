"""Adapter tampilan skill: memakai SkillRegistry repo, bukan loader kedua."""

from __future__ import annotations

from wth_ai.skills.registry import SkillRegistry


def skill_summary() -> str:
    registry = SkillRegistry()
    registry.discover()
    skills = registry.list_skills()
    if not skills:
        return "tidak ada skill ditemukan (./skills, $WTH_HOME/skills)"
    lines = [f"{len(skills)} skill"]
    for skill in skills:
        lines.append(f"  {skill.id} — {skill.description or '(tanpa deskripsi)'}")
    return "\n".join(lines)


def skill_fragment(skill_id: str, *, max_chars: int = 4000) -> str:
    """Badan skill untuk prompt. Tidak pernah dieksekusi."""
    registry = SkillRegistry()
    registry.discover()
    skill = registry.get(skill_id)
    if skill is None:
        return ""
    return skill.load_content(max_chars)