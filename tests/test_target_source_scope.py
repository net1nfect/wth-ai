"""Scope card berjenis `source` untuk pemindaian codebase."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wth_ai.scope_card import ScopeCard, ScopeCardError


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return root


def test_source_card_valid(tmp_path: Path) -> None:
    root = _project(tmp_path)
    card = ScopeCard(target="proj", kind="source", source_path=str(root), authorized=True)
    card.validate()
    assert card.to_dict()["kind"] == "source"


def test_source_card_requires_path(tmp_path: Path) -> None:
    card = ScopeCard(target="proj", kind="source", authorized=True)
    with pytest.raises(ScopeCardError, match="source_path"):
        card.validate()


def test_source_card_requires_absolute_dir(tmp_path: Path) -> None:
    root = _project(tmp_path)
    card = ScopeCard(target="proj", kind="source", source_path="relatif/proj", authorized=True)
    with pytest.raises(ScopeCardError, match="absolut"):
        card.validate()

    file_path = root / "app.py"
    card = ScopeCard(target="proj", kind="source", source_path=str(file_path), authorized=True)
    with pytest.raises(ScopeCardError, match="direktori"):
        card.validate()


def test_source_card_needs_authorized(tmp_path: Path) -> None:
    root = _project(tmp_path)
    card = ScopeCard(target="proj", kind="source", source_path=str(root), authorized=False)
    with pytest.raises(ScopeCardError, match="authorized"):
        card.validate()


def test_source_card_roundtrip(tmp_path: Path) -> None:
    root = _project(tmp_path)
    path = tmp_path / "scope_card.json"
    ScopeCard(
        target="proj", kind="source", source_path=str(root), authorized=True
    ).write(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "source"
    assert data["source_path"] == str(root)
    assert ScopeCard.load(path).kind == "source"


def test_network_card_still_requires_cidr_or_domain() -> None:
    card = ScopeCard(target="example.com", authorized=True)
    with pytest.raises(ScopeCardError, match="CIDR atau domain"):
        card.validate()
