"""Skill BOLA bawaan terbaca oleh registry (tanpa dieksekusi)."""

from pathlib import Path

from wth_ai.skills.registry import SkillRegistry

REPO = Path(__file__).resolve().parents[1]


def test_bola_skill_is_discovered(monkeypatch):
    monkeypatch.chdir(REPO)
    registry = SkillRegistry()
    registry.discover()
    skill = registry.get("bola-api-testing")
    assert skill is not None, registry.skills.keys()
    assert "bola" in (skill.description or "").lower() or "BOLA" in skill.description
    assert skill.load_content(200)


def test_skill_is_text_only(monkeypatch):
    """Skill tidak dieksekusi: isinya markdown, bukan perintah shell."""
    monkeypatch.chdir(REPO)
    text = (REPO / "skills" / "bola-api-testing" / "SKILL.md").read_text()
    assert text.startswith("---")
    assert "subprocess" not in text and "os.system" not in text