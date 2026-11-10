"""`wth-ai scope --target H --authorized` tidak boleh menolak dirinya sendiri.

Item 1: dokumentasi AGENTS.md §4 memasang perintah tanpa `--cidr`, tetapi
`ScopeCard.validate()` menuntut minimal satu CIDR atau domain, sehingga
`--target` saja selalu berakhir "butuh minimal satu CIDR atau domain".
"""

from __future__ import annotations

import ipaddress

from wth_ai.scope_card import ScopeCard

def test_target_only_network_card_derives_scope_from_target():
    """Card target-only: scope diturunkan dari target, bukan ditolak."""
    card = ScopeCard(target="172.28.0.10", authorized=True)
    card.validate()

    assert card.cidr == ["172.28.0.10/32"]
    assert ipaddress.ip_network(card.cidr[0], strict=False).num_addresses == 1
    # Target ASLI tidak berubah: kartu tetap merekam apa yang operator tulis.
    assert card.target == "172.28.0.10"

def test_target_only_hostname_card_records_domain():
    """Hostname: target menjadi entri domain in-scope."""
    card = ScopeCard(target="app.example.com", authorized=True)
    card.validate()

    assert card.domains == ["app.example.com"]
    assert card.cidr == []

def test_target_with_url_or_port_is_normalized():
    """Target berbentuk URL/port disimpan sebagai host, bukan teks mentah."""
    import ipaddress

    for raw, expect in (
        ("https://app.example.com/orders?id=1", "app.example.com"),
        ("172.28.0.10:8443", "172.28.0.10"),
        ("*.example.com", "*.example.com"),
    ):
        card = ScopeCard(target=raw, authorized=True)
        card.validate()
        recorded = card.cidr + card.domains
        if raw.startswith("*."):
            # Wildcard disimpan apa adanya: itu bentuk kanoniknya.
            assert card.domains == [raw]
            continue
        assert raw not in recorded, f"{raw} tersimpan mentah: {recorded}"
        if expect in recorded:
            continue
        # IP literal direkam sebagai prefix /32 (atau /128), bukan host polos.
        assert any(
            str(ipaddress.ip_network(entry, strict=False).network_address) == expect
            and ipaddress.ip_network(entry, strict=False).num_addresses == 1
            for entry in card.cidr
        ), f"{raw} -> {recorded}"

def test_explicit_scope_is_never_widened_by_target():
    """CIDR/domain eksplisit menang; target di luar itu tidak ditambahkan."""
    card = ScopeCard(target="10.0.0.5", cidr=["172.28.0.0/16"],
                     domains=["example.com"], authorized=True)
    card.validate()

    assert card.cidr == ["172.28.0.0/16"]
    assert card.domains == ["example.com"]

def test_unauthorized_still_rejected():
    """Fallback tidak boleh melonggarkan izin: authorized tetap wajib."""
    import pytest

    from wth_ai.scope_card import ScopeCardError

    with pytest.raises(ScopeCardError):
        ScopeCard(target="172.28.0.10", authorized=False).validate()
