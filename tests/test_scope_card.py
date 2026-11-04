"""ScopeCard: menolak scope tidak jujur, menulis atomik."""

import json

import pytest

from wth_ai.scope_card import ScopeCard, ScopeCardError


def test_refuses_without_authorization(tmp_path):
    card = ScopeCard(target="172.28.0.10", cidr=["172.28.0.0/16"])
    with pytest.raises(ScopeCardError):
        card.write(tmp_path / "scope_card.json")


def test_refuses_without_any_scope(tmp_path):
    card = ScopeCard(target="172.28.0.10", authorized=True)
    with pytest.raises(ScopeCardError):
        card.write(tmp_path / "scope_card.json")


def test_writes_atomically_and_reloads(tmp_path):
    card = ScopeCard(target="172.28.0.10", cidr=["172.28.0.0/16"], authorized=True,
                     mode="auto", destructive=False)
    out = card.write(tmp_path / "scope_card.json")
    data = json.loads(out.read_text())
    assert data["target"] == "172.28.0.10"
    assert data["authorized"] is True
    assert data["created_at"]
    assert not (tmp_path / "scope_card.json.tmp").exists()
    assert ScopeCard.load(out).mode == "auto"