"""Recon fetch menghormati gerbang egress yang dipasang pemanggil."""

import wth_ai.recon.http as rhttp


def test_recon_fetch_honours_egress_guard(monkeypatch, tmp_path):
    from wth_ai.netguard import EgressGuard

    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    rhttp.set_egress_guard(EgressGuard.from_patterns(["172.28.0.0/16"]))
    try:
        res = rhttp.fetch("https://example.com/")
    finally:
        rhttp.set_egress_guard(None)
    assert "egress" in (res.error or "").lower()