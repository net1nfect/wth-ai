"""9Router vendor skills bundle tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.skills.hub import NINEROUTER_SKILL_IDS, SkillHub
from wth_ai.skills.registry import SkillRegistry


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    repo_skills = Path(__file__).resolve().parents[1] / "skills"
    for skill_id in NINEROUTER_SKILL_IDS:
        src = repo_skills / skill_id
        if not src.exists():
            pytest.skip("bundled 9router skills not present")
        dest = tmp_path / skill_id
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text((src / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_ninerouter_skill_ids_count():
    assert len(NINEROUTER_SKILL_IDS) == 8


def test_discover_all_9router_skills(skills_dir: Path):
    reg = SkillRegistry(local_path=skills_dir, openclaw_path=Path("/nonexistent"))
    count = reg.discover()
    assert count == 8
    for skill_id in NINEROUTER_SKILL_IDS:
        skill = reg.get(skill_id)
        assert skill is not None, skill_id
        assert skill.name
        assert "9router" in skill.id or skill.id == "9router"


def test_9router_entry_skill_content(skills_dir: Path):
    reg = SkillRegistry(local_path=skills_dir, openclaw_path=Path("/nonexistent"))
    reg.discover()
    entry = reg.get("9router")
    assert entry is not None
    body = entry.load_content(4000)
    assert "NINEROUTER_URL" in body
    assert "9router-chat" in body


def test_skills_doctor_reports_9router(skills_dir: Path):
    cfg = WTHConfig()
    cfg.skills.local_path = skills_dir
    reg = SkillRegistry(local_path=skills_dir, openclaw_path=Path("/nonexistent"))
    hub = SkillHub(cfg, reg)
    out = hub.doctor()
    assert "9router pack: 8/8" in out


def test_sync_9router_skips_when_present(skills_dir: Path):
    cfg = WTHConfig()
    cfg.skills.local_path = skills_dir
    reg = SkillRegistry(local_path=skills_dir, openclaw_path=Path("/nonexistent"))
    hub = SkillHub(cfg, reg)
    out = hub.sync_9router()
    assert "Skipped" in out
    assert "Updated: 0" in out


def test_skills_search_9router(skills_dir: Path):
    reg = SkillRegistry(local_path=skills_dir, openclaw_path=Path("/nonexistent"))
    reg.discover()
    hits = reg.search("9router", limit=8)
    ids = {s.id for s in hits}
    assert "9router" in ids
    assert len(ids) >= 4
