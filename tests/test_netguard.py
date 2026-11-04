"""Netguard: allowlist egress + audit EGRESS_DENIED."""

import json

import pytest

from wth_ai.netguard import EgressDenied, EgressGuard


def _guard():
    return EgressGuard.from_patterns(["172.28.0.0/16", "*.example.com", "lab.local"])


def test_allows_cidr_member():
    assert _guard().allows("172.28.0.5") is True


def test_allows_wildcard_domain():
    assert _guard().allows("api.example.com") is True
    assert _guard().allows("example.com") is True


def test_allows_exact_host():
    assert _guard().allows("lab.local") is True


def test_denies_public_ip():
    assert _guard().allows("1.1.1.1") is False


def test_denies_unknown_domain():
    assert _guard().allows("evil.test") is False


def test_check_raises_and_audits(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    with pytest.raises(EgressDenied):
        _guard().check("1.1.1.1")
    log = (tmp_path / "logs" / "audit.log").read_text()
    assert "EGRESS_DENIED" in log
    assert json.loads(log.splitlines()[-1])["host"] == "1.1.1.1"