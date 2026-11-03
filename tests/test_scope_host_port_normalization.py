"""Regresi: target/scope bertanda PORT harus cocok dengan host-nya sendiri.

Terukur dari UAT CLI nyata: `/target 127.0.0.1:34621` lalu `/recon quick` ->
`Blocked: Host 127.0.0.1 tidak cocok dengan in-scope list`. `is_in_scope()` menormalisasi
host yang DIPERIKSA (port dibuang) tapi tidak menormalisasi POLA in-scope, sehingga target
lokal/staging bertanda port tidak pernah bisa diuji di scope-nya sendiri.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.scope_manager import ScopeManager


def _manager(target: str, *, in_scope=None) -> ScopeManager:
    manager = ScopeManager()
    manager.set_scope(target=target, authorized=True, in_scope=in_scope or [target])
    return manager


def test_host_with_port_matches_its_own_scope():
    manager = _manager("127.0.0.1:34621")
    ok, reason = manager.is_in_scope("http://127.0.0.1:34621/api/user?id=1")
    assert ok, reason


def test_scheme_and_path_in_the_pattern_do_not_break_matching():
    manager = _manager("https://staging.example.com:8443/app", in_scope=["https://staging.example.com:8443/app"])
    ok, reason = manager.is_in_scope("https://staging.example.com:8443/deep/path")
    assert ok, reason


def test_wildcard_patterns_still_work():
    manager = _manager("example.com", in_scope=["*.example.com"])
    assert manager.is_in_scope("https://a.example.com/x")[0] is True
    assert manager.is_in_scope("https://example.com/x")[0] is True
    assert manager.is_in_scope("https://evil.com/x")[0] is False


def test_out_of_scope_with_a_port_is_still_excluded():
    manager = ScopeManager()
    manager.set_scope(target="example.com", in_scope=["*.example.com"],
                      out_of_scope=["admin.example.com:8443"])
    ok, reason = manager.is_in_scope("https://admin.example.com:8443/panel")
    assert ok is False and "out of scope" in reason.lower()


def test_unrelated_host_still_rejected():
    manager = _manager("127.0.0.1:34621")
    ok, reason = manager.is_in_scope("http://127.0.0.1:9999/other")
    assert ok is True, "port lain pada host yang sama tetap host yang sama"

    manager2 = _manager("example.com")
    assert manager2.is_in_scope("https://notexample.com/x")[0] is False
