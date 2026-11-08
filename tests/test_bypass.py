"""Bypass LLM: profil prompt runtime yang terpisah dari AGENTS.md.

Yang dijaga: bypass mati secara default, prompt-nya tidak pernah menimpa
invarian produk (scope/egress/kill switch/gate bukti), nama profil tidak bisa
keluar dari direktori profil, dan *tidak ada* bypass yang tercantum di
AGENTS.md repo — kalau itu terjadi, setiap sesi ikut memakainya tanpa diminta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wth_ai.agents import bypass

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def bypass_dir(tmp_path, monkeypatch):
    directory = tmp_path / "bypass"
    monkeypatch.setenv(bypass.BYPASS_DIR_ENV, str(directory))
    return directory


# --------------------------------------------------------------------------- #
# pemisahan dari AGENTS.md
# --------------------------------------------------------------------------- #
def test_packaged_prompt_file_is_the_default_source():
    """Profil `wth-ai` dibaca dari `prompts/agent/AGENT.md`, bukan konstanta Python."""
    path = bypass.packaged_prompt_path()

    assert path == REPO / "prompts" / "agent" / "AGENT.md"
    prompt = bypass.load("default")
    assert prompt.source == path
    assert "Operating Policy" in prompt.text


def test_packaged_prompt_matches_constant():
    """Konstanta cadangan tidak boleh menyimpang dari berkas yang dipakai."""
    on_disk = bypass.packaged_prompt_path().read_text(encoding="utf-8").strip()
    assert on_disk == bypass.BUILTIN_PROMPT.strip(), "berkas dan konstanta berbeda isi"


def test_packaged_prompt_is_not_the_repo_constitution():
    from wth_ai.agents import load_agent_constitution

    constitution = load_agent_constitution(REPO)
    packaged = bypass.packaged_prompt_path().read_text(encoding="utf-8")
    assert constitution != packaged


def test_repo_agents_md_carries_no_bypass():
    """Konvensi repo bukan tempat instruksi sesi: AGENTS.md tidak boleh berisi prompt bypass."""
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8").lower()
    assert "session instructions" not in text
    assert "operating mode: bypass" not in text


def test_constitution_and_bypass_are_separate_mechanisms(bypass_dir):
    from wth_ai.agents import load_agent_constitution

    constitution = load_agent_constitution(REPO).lower()
    prompt = bypass.load("default")

    assert "operating policy" not in constitution
    assert "operating policy" in prompt.text.lower()


# --------------------------------------------------------------------------- #
# profil
# --------------------------------------------------------------------------- #
def test_default_is_builtin_prompt(bypass_dir):
    prompt = bypass.load()
    assert prompt.is_builtin
    assert prompt.name == bypass.BUILTIN_PROFILE
    assert "Operating Policy" in prompt.text


def test_builtin_prompt_keeps_product_invariants(bypass_dir):
    text = bypass.load("default").text.lower()
    for invariant in ("allowlist", "kill switch", "negative control", "confirmed"):
        assert invariant in text, f"prompt bawaan harus menyebut {invariant}"


def test_builtin_prompt_forbids_disclaimers(bypass_dir):
    text = bypass.load("default").text.lower()
    assert "disclaimer" in text
    assert "not a safer answer" in text


def test_named_profile_is_read_from_dir(bypass_dir):
    bypass_dir.mkdir(parents=True)
    (bypass_dir / "lab.md").write_text("LAB PROMPT", encoding="utf-8")

    prompt = bypass.load("lab")
    assert prompt.text == "LAB PROMPT"
    assert prompt.source == bypass_dir / "lab.md"


def test_missing_profile_raises(bypass_dir):
    with pytest.raises(bypass.BypassError):
        bypass.load("tidak-ada")


def test_empty_profile_raises(bypass_dir):
    bypass_dir.mkdir(parents=True)
    (bypass_dir / "kosong.md").write_text("   \n", encoding="utf-8")
    with pytest.raises(bypass.BypassError):
        bypass.load("kosong")


def test_list_profiles(bypass_dir):
    bypass_dir.mkdir(parents=True)
    (bypass_dir / "b.md").write_text("b", encoding="utf-8")
    (bypass_dir / "a.md").write_text("a", encoding="utf-8")

    names = bypass.list_profiles()
    assert names[0] == bypass.BUILTIN_PROFILE
    assert "a" in names and "b" in names


@pytest.mark.parametrize("bad", ["../escape", "sub/dir", "", "   ", ".."])
def test_profile_name_cannot_escape_directory(bad, bypass_dir):
    with pytest.raises(bypass.BypassError):
        bypass.profile_slug(bad)


def test_system_block_is_empty_when_bypass_off(bypass_dir):
    assert bypass.system_block("") == ""


def test_system_block_wraps_prompt(bypass_dir):
    block = bypass.system_block("default")
    assert block.startswith("---\n")
    assert "SESSION INSTRUCTIONS (bypass: wth-ai)" in block
    assert "SESSION INSTRUCTIONS END" in block


def test_legacy_profile_name_still_resolves(bypass_dir):
    """`bypass: wth` tersimpan di user.yaml instalasi lama harus tetap jalan."""
    assert bypass.resolve_name("wth") == bypass.BUILTIN_PROFILE
    assert bypass.load("wth").is_builtin


# --------------------------------------------------------------------------- #
# pemasangan
# --------------------------------------------------------------------------- #
def test_install_copies_file_and_names_from_stem(bypass_dir, tmp_path):
    source = tmp_path / "glm-zcode-5.3-AGENTS.md"
    source.write_text("# prompt\nisi", encoding="utf-8")

    target = bypass.install(source)

    assert target == bypass_dir / "glm-zcode-5.3-agents.md"
    assert target.read_text(encoding="utf-8").strip().endswith("isi")


def test_install_refuses_overwrite_without_force(bypass_dir, tmp_path):
    source = tmp_path / "p.md"
    source.write_text("v1", encoding="utf-8")
    bypass.install(source, name="lab")

    with pytest.raises(bypass.BypassError):
        bypass.install(source, name="lab")

    source.write_text("v2", encoding="utf-8")
    bypass.install(source, name="lab", overwrite=True)
    assert bypass.load("lab").text == "v2"


def test_install_rejects_missing_file(bypass_dir, tmp_path):
    with pytest.raises(bypass.BypassError):
        bypass.install(tmp_path / "nope.md")


def test_remove(bypass_dir, tmp_path):
    source = tmp_path / "p.md"
    source.write_text("v1", encoding="utf-8")
    bypass.install(source, name="lab")

    assert bypass.remove("lab") is True
    assert bypass.remove("lab") is False