"""Isolasi sumber SkillRegistry — `discover()` tidak boleh menyentuh WTH_HOME
kecuali sumbernya diminta eksplisit.

Konteks: dua test registry lama tidak hermetis karena `discover()` SELALU
menambahkan `$WTH_HOME/skills`, sehingga hitungannya bergantung pada isi mesin
operator (7 skill auto nyata membuat hitungan 15 dan 9 alih-alih 8 dan 2).
Test di sini mengunci kontrak: `sources` eksplisit = tidak menyentuh WTH_HOME;
default = tetap menyertakannya (dipakai `wth_ai/skills/autowrite.py`).
"""

from __future__ import annotations

from pathlib import Path

from wth_ai.skills.registry import SkillRegistry


def _write_skill(base: Path, slug: str, name: str) -> None:
    d = base / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing. ok.\n---\n\n# x\n",
        encoding="utf-8",
    )


def test_discover_ignores_wth_home_when_sources_given(tmp_path, monkeypatch):
    local = tmp_path / "local"
    _write_skill(local, "langsung", "langsung")

    fake_home = tmp_path / "wth-home"
    _write_skill(fake_home / "skills", "auto-satu", "auto satu")
    _write_skill(fake_home / "skills" / "auto", "auto-dua", "auto dua")
    monkeypatch.setenv("WTH_HOME", str(fake_home))

    reg = SkillRegistry(
        openclaw_path=tmp_path / "tidak-ada",
        local_path=local,
        sources=("local",),
    )
    assert reg.discover() == 1
    assert reg.get("langsung") is not None
    assert reg.get("auto-satu") is None
    assert reg.get("auto-dua") is None


def test_discover_includes_wth_home_by_default(tmp_path, monkeypatch):
    local = tmp_path / "local"
    _write_skill(local, "langsung", "langsung")

    fake_home = tmp_path / "wth-home"
    _write_skill(fake_home / "skills", "auto-satu", "auto satu")
    _write_skill(fake_home / "skills" / "auto", "auto-dua", "auto dua")
    monkeypatch.setenv("WTH_HOME", str(fake_home))

    reg = SkillRegistry(openclaw_path=tmp_path / "tidak-ada", local_path=local)
    assert reg.discover() == 3
    assert reg.get("auto-satu") is not None
    assert reg.get("auto-dua") is not None
