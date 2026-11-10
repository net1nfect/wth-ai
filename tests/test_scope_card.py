"""ScopeCard: menolak scope tidak jujur, menulis atomik."""

import json

import pytest

from wth_ai.scope_card import ScopeCard, ScopeCardError


def test_refuses_without_authorization(tmp_path):
    card = ScopeCard(target="172.28.0.10", cidr=["172.28.0.0/16"])
    with pytest.raises(ScopeCardError):
        card.write(tmp_path / "scope_card.json")


def test_target_only_card_derives_single_host_scope(tmp_path):
    """Target tunggal kini diturunkan jadi scope /32, bukan ditolak.

    Lihat tests/test_scope_card_target_fallback.py: bentuk
    `scope --target H --authorized` didokumentasikan di AGENTS.md §4 dan memang
    tidak menambah izin apa pun di luar host itu.
    """
    card = ScopeCard(target="172.28.0.10", authorized=True)
    card.write(tmp_path / "scope_card.json")
    assert card.cidr == ["172.28.0.10/32"]
    assert card.target == "172.28.0.10"


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