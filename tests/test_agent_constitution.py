"""Konstitusi agen dibaca dari AGENTS.md (fallback AGENT.md untuk repo lama)."""

from pathlib import Path

from wth_ai.agents import CONSTITUTION_FILES, load_agent_constitution

REPO = Path(__file__).resolve().parents[1]


def test_prefers_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("konvensi baru")
    (tmp_path / "AGENT.md").write_text("konstitusi lama")
    assert load_agent_constitution(tmp_path) == "konvensi baru"


def test_falls_back_to_agent_md(tmp_path):
    (tmp_path / "AGENT.md").write_text("konstitusi lama")
    assert load_agent_constitution(tmp_path) == "konstitusi lama"


def test_missing_constitution_returns_empty(tmp_path):
    assert load_agent_constitution(tmp_path) == ""


def test_repo_constitution_is_loaded_and_not_stale_prompt():
    text = load_agent_constitution(REPO)
    assert text, "repo harus punya AGENTS.md"
    assert "konvensi kerja" in text.lower()
    assert CONSTITUTION_FILES[0] == "AGENTS.md"


def test_repo_has_no_legacy_agent_md():
    """AGENT.md sudah dilebur ke AGENTS.md; jangan hidupkan lagi."""
    assert not (REPO / "AGENT.md").exists()
    assert (REPO / "AGENTS.md").is_file()
    assert (REPO / "CLAUDE.md").is_file()


def test_claude_md_points_to_agents_md():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    assert "pytest tests" in text