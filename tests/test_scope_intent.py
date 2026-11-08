"""Scope intent parsing tests."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.scope_intent import parse_scope_intent


def test_parse_set_target_url():
    args = parse_scope_intent("set target https://universitas-a.ssc.sevimaplatform.com/gate/login")
    assert args is not None
    assert args["target"] == "universitas-a.ssc.sevimaplatform.com"
    assert args["authorized"] is True
    assert "universitas-a.ssc.sevimaplatform.com" in args["in_scope"]
    assert "entry_url=" in args.get("notes", "")


def test_parse_scope_domain_authorized():
    args = parse_scope_intent("scope example.com authorized")
    assert args is not None
    assert args["target"] == "example.com"


def test_parse_bare_url():
    args = parse_scope_intent("https://foo.example.org/path")
    assert args is not None
    assert args["target"] == "foo.example.org"


def test_parse_unrelated_returns_none():
    assert parse_scope_intent("hello world") is None


# P1-6: `target\s+(.+)` rakus -- "target host authorized" menghasilkan target
# `'host authorized'`, dan bentuk ber-slash ("/target host") tidak dikenali
# sama sekali. Matriks ini mengunci normalisasinya.
@pytest.mark.parametrize("text,target", [
    ("target example.com authorized", "example.com"),
    ("/target example.com authorized", "example.com"),
    ("/target example.com", "example.com"),
    ("/scope set example.com", "example.com"),
    ("scope example.com authorized", "example.com"),
    ("scope example.com", "example.com"),
    ("/target 10.0.0.5 authorized", "10.0.0.5"),
    ("/target https://app.example.com/login authorized", "app.example.com"),
    ("set target example.com", "example.com"),
    ("TARGET EXAMPLE.COM AUTHORIZED", "example.com"),
])
def test_target_is_normalized_without_the_authorized_noise(text, target):
    args = parse_scope_intent(text)
    assert args is not None, text
    assert args["target"] == target
    assert args["authorized"] is True
    assert target in args["in_scope"]
    assert not any("authorized" in str(entry) for entry in args["in_scope"])


def test_supervisor_fast_scope_via_orchestrator():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/target https://test.example.com/login")
    assert "test.example.com" in out
    assert orch.state.scope.target == "test.example.com"
