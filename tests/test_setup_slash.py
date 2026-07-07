"""Slash setup command tests — /target, /model, /provider, /profile, /ui."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")
os.environ.setdefault("WTH_SKIP_SETUP", "1")

from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


def test_target_slash_url():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/target https://test.example.com/login")
    assert "test.example.com" in out
    assert orch.state.scope.target == "test.example.com"
    assert orch.state.scope.authorized is True


def test_target_show():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    orch.handle_input("/target example.com")
    out = orch.handle_input("/target")
    assert "example.com" in out


def test_target_clear():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    orch.handle_input("/target example.com")
    out = orch.handle_input("/target clear")
    assert "cleared" in out.lower()
    assert not orch.state.scope.target


def test_nl_set_target_still_works_with_tip():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("set target https://foo.example.org/path")
    assert "foo.example.org" in out
    assert "/target" in out
    assert orch.state.scope.target == "foo.example.org"


def test_model_show():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/model")
    assert "Models" in out or "planner" in out


def test_model_list_offline():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    with patch.object(orch.client, "list_models", return_value=["alpha", "beta", "gamma"]):
        out = orch.handle_input("/model list 9router")
    assert "alpha" in out
    assert "1." in out


def test_model_pick_by_index():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    with patch.object(orch.client, "list_models", return_value=["one", "two", "Hacker"]):
        orch.handle_input("/model list 9router")
        out = orch.handle_input("/model 3")
    assert "Hacker" in out
    assert orch.config.providers["planner"].model == "Hacker"


def test_provider_show():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/provider")
    assert "planner" in out


def test_profile_list():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/profile list")
    assert "Profiles" in out or "profile" in out.lower()


def test_ui_show():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/ui")
    assert "ui.mode" in out or "compact" in out


def test_help_includes_target_and_model():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/help")
    assert "/target" in out
    assert "/model" in out


def test_registry_has_setup_commands():
    from wth_ai.platform.registry import get_platform_registry
    names = get_platform_registry().all_slash_names()
    for cmd in ("/target", "/model", "/provider", "/profile", "/ui"):
        assert cmd in names
