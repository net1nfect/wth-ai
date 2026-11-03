"""Skill hub — install, list, use official packs."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.config import WTHConfig
from wth_ai.skills.guard import scan_skill_path
from wth_ai.skills.registry import SkillRegistry
from wth_ai.workspace import wth_home

NINEROUTER_SKILLS_REPO = "https://github.com/decolua/9router/tree/master/skills"
NINEROUTER_RAW_BASE = "https://raw.githubusercontent.com/decolua/9router/refs/heads/master/skills"
NINEROUTER_SKILL_IDS: tuple[str, ...] = (
    "9router",
    "9router-chat",
    "9router-embeddings",
    "9router-image",
    "9router-stt",
    "9router-tts",
    "9router-web-fetch",
    "9router-web-search",
)


class SkillHub:
    def __init__(self, config: WTHConfig, registry: SkillRegistry) -> None:
        self.config = config
        self.registry = registry
        self.hub_dir = wth_home() / "skills" / ".hub"
        self.official_src = Path("skills/official")
        self.audit_log = self.hub_dir / "audit.log"

    def _audit(self, msg: str) -> None:
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with self.audit_log.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts} {msg}\n")

    def list_text(self, category: str | None = None) -> str:
        self.registry.discover()
        skills = list(self.registry.skills.values())
        if category:
            skills = [s for s in skills if category in s.tags or category in s.id]
        lines = [f"## Skills ({len(skills)})", ""]
        for s in skills[:40]:
            lines.append(f"- `{s.id}` — {s.name} [{s.source}]")
        if len(skills) > 40:
            lines.append(f"\n... and {len(skills) - 40} more")
        return "\n".join(lines)

    def search_text(self, query: str) -> str:
        matches = self.registry.search(query, limit=15)
        if not matches:
            return f"No skills match '{query}'"
        return "\n".join(f"- `{s.id}` — {s.description[:80]}" for s in matches)

    def install(self, rel_path: str) -> str:
        rel = rel_path.replace("official/", "").strip("/")
        src = self.official_src / rel
        if not src.exists():
            # try nested official/security/web-pentest
            for candidate in self.official_src.rglob("SKILL.md"):
                if rel in str(candidate.parent):
                    src = candidate.parent
                    break
        if not src.exists():
            return f"Skill pack not found: {rel_path}"
        report = scan_skill_path(src)
        if report.blocked:
            return f"Install blocked: {report.reason}"
        dest = self.config.skills.local_path / src.name
        if dest.exists():
            return f"Already installed: {dest.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        self._audit(f"install {rel_path} -> {dest}")
        self.registry.discover()
        return f"Installed skill: **{dest.name}**"

    def use(self, orch: Any, skill_id: str) -> str:
        skill = self.registry.get(skill_id)
        if not skill:
            matches = self.registry.search(skill_id, limit=1)
            skill = matches[0] if matches else None
        if not skill:
            return f"Skill not found: {skill_id}"
        active = orch.state.metadata.setdefault("active_skills", [])
        if skill.id not in active:
            active.append(skill.id)
        orch._persist()
        return f"Active skill: **{skill.id}** — {skill.name}"

    def sync_9router(self, *, force: bool = False) -> str:
        """Download/update all 9Router skills from upstream GitHub."""
        import json
        import urllib.error
        import urllib.request

        dest_root = self.config.skills.local_path
        dest_root.mkdir(parents=True, exist_ok=True)
        updated: list[str] = []
        skipped: list[str] = []
        blocked: list[str] = []

        for skill_id in NINEROUTER_SKILL_IDS:
            dest = dest_root / skill_id
            skill_md = dest / "SKILL.md"
            if skill_md.exists() and not force:
                skipped.append(skill_id)
                continue
            dest.mkdir(parents=True, exist_ok=True)
            url = f"{NINEROUTER_RAW_BASE}/{skill_id}/SKILL.md"
            try:
                with urllib.request.urlopen(url, timeout=45) as resp:  # noqa: S310  # nosec B310
                    text = resp.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                return f"9router sync failed on `{skill_id}`: {exc}"
            skill_md.write_text(text, encoding="utf-8")
            report = scan_skill_path(dest)
            if report.blocked:
                skill_md.unlink(missing_ok=True)
                blocked.append(f"{skill_id} ({report.reason})")
                continue
            updated.append(skill_id)

        source_meta = dest_root / "9router" / "SOURCE.json"
        if source_meta.parent.exists():
            source_meta.write_text(
                json.dumps(
                    {
                        "upstream": NINEROUTER_SKILLS_REPO,
                        "vendor": "decolua/9router",
                        "skills": list(NINEROUTER_SKILL_IDS),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        self._audit(f"sync_9router updated={updated} skipped={skipped} blocked={blocked}")
        self.registry.discover()
        lines = [
            "## 9router skills sync",
            f"- Updated: {len(updated)}",
            f"- Skipped (already present): {len(skipped)}",
            f"- Total 9router skills: {sum(1 for s in self.registry.skills if s.startswith('9router'))}",
            f"- Upstream: {NINEROUTER_SKILLS_REPO}",
        ]
        if updated:
            lines.append(f"- Installed: {', '.join(updated)}")
        if blocked:
            lines.append(f"- Blocked: {', '.join(blocked)}")
        lines.append("\nUse `/skills use 9router` or `/skill 9router-chat`")
        return "\n".join(lines)

    def doctor(self) -> str:
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        count = self.registry.discover()
        official = len(list(self.official_src.rglob("SKILL.md"))) if self.official_src.exists() else 0
        nine = sum(1 for sid in self.registry.skills if sid.startswith("9router"))
        return (
            f"## Skills doctor\n"
            f"- Discovered: {count}\n"
            f"- 9router pack: {nine}/{len(NINEROUTER_SKILL_IDS)}\n"
            f"- Official packs: {official}\n"
            f"- Hub dir: {self.hub_dir}\n"
            f"- Audit log: {'present' if self.audit_log.exists() else 'empty'}"
        )
