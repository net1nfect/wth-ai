"""UX: target cukup domain/wildcard; authorization satu kata eksplisit."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")
os.environ.setdefault("WTH_SKIP_SETUP", "1")

from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator
from wth_ai.scope_intent import parse_scope_intent


def test_bare_domain_infers_scope():
    intent = parse_scope_intent("example.com")
    assert intent is not None
    assert intent["target"] == "example.com"
    assert intent["in_scope"] == ["example.com", "*.example.com"]
    assert intent["authorized"] is False


def test_wildcard_input_uses_root_target_and_wildcard_scope():
    intent = parse_scope_intent("*.app.example.com")
    assert intent is not None
    assert intent["target"] == "app.example.com"
    assert intent["in_scope"] == ["*.app.example.com"]
    assert intent["authorized"] is False


def test_authorized_suffix_is_stripped_from_target():
    intent = parse_scope_intent("set target example.com authorized")
    assert intent is not None
    assert intent["target"] == "example.com"
    assert intent["authorized"] is True


def test_negated_suffix_does_not_grant_and_is_stripped():
    intent = parse_scope_intent("target example.com not authorized")
    assert intent is not None
    assert intent["target"] == "example.com"
    assert intent["authorized"] is False


def test_short_target_then_authorize():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/target example.com")
    assert "authorized=no" in out
    assert "/authorize" in out
    assert orch.state.scope.in_scope == ["example.com", "*.example.com"]

    out = orch.handle_input("/authorize")
    assert "authorized" in out
    assert orch.state.scope.authorized is True


def test_authorize_revokes():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    orch.handle_input("/target example.com")
    orch.handle_input("/authorize")
    orch.handle_input("/authorize off")
    assert orch.state.scope.authorized is False


def test_one_line_domain_plus_authorized():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/target example.com authorized")
    assert orch.state.scope.target == "example.com"
    assert orch.state.scope.authorized is True
    assert "authorized=yes" in out
