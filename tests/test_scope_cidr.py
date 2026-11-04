"""Scope CIDR: pola subnet harus bertahan normalisasi dan cocok ke host."""

from wth_ai.scope_manager import ScopeManager, _cidr_matches


def test_cidr_pattern_is_not_mangled():
    sm = ScopeManager()
    assert sm._normalize_pattern("172.28.0.0/16") == "172.28.0.0/16"


def test_hostname_pattern_still_normalized():
    sm = ScopeManager()
    assert sm._normalize_pattern("https://Example.com:8443/x") == "example.com"
    assert sm._normalize_pattern("*.example.com") == "*.example.com"


def test_cidr_match_helper():
    assert _cidr_matches("172.28.0.5", "172.28.0.0/16") is True
    assert _cidr_matches("172.29.0.5", "172.28.0.0/16") is False
    assert _cidr_matches("8.8.8.8", "172.28.0.0/16") is False
    assert _cidr_matches("example.com", "172.28.0.0/16") is False


def test_is_in_scope_accepts_cidr_member():
    sm = ScopeManager()
    sm.set_scope(target="172.28.0.10", authorized=True, in_scope=["172.28.0.0/16"])
    assert sm.is_in_scope("http://172.28.0.5:8080/api")[0] is True


def test_is_in_scope_rejects_outside_cidr():
    sm = ScopeManager()
    sm.set_scope(target="172.28.0.10", authorized=True, in_scope=["172.28.0.0/16"])
    assert sm.is_in_scope("http://8.8.8.8/")[0] is False


def test_is_in_scope_hostname_still_works():
    sm = ScopeManager()
    sm.set_scope(target="example.com", authorized=True, in_scope=["example.com"])
    assert sm.is_in_scope("https://example.com/a")[0] is True


def test_out_of_scope_cidr_wins():
    sm = ScopeManager()
    sm.set_scope(target="172.28.0.10", authorized=True,
                 in_scope=["172.28.0.0/16"], out_of_scope=["172.28.9.0/24"])
    assert sm.is_in_scope("http://172.28.9.5/")[0] is False