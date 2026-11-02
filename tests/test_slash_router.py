"""Slash router tests."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")


@pytest.fixture
def orch():
    from wth_ai.orchestrator import WTHOrchestrator
    o = WTHOrchestrator(fresh=True)
    return o


def test_slash_help_not_supervisor(orch):
    with patch.object(orch.supervisor, "run") as mock_run:
        out = orch.handle_input("/help")
        mock_run.assert_not_called()
    assert "Slash Commands" in out or "WTH-AI" in out


def test_slash_status(orch):
    out = orch.handle_input("/status")
    assert "Session" in out or "WTH-AI Status" in out


def test_slash_subagents_list(orch):
    out = orch.handle_input("/subagents list")
    assert "injection" in out or "Subagents" in out


def test_slash_skills_list(orch):
    out = orch.handle_input("/skills list")
    assert "Skills" in out


def test_slash_config_show(orch):
    out = orch.handle_input("/config show")
    assert "Config" in out


def test_natural_language_goes_supervisor(orch):
    with patch.object(orch.chat, "respond", return_value={"raw": "hello chat", "model": "test"}) as mock_chat, patch.object(
        orch.supervisor, "run", return_value="hello agent"
    ) as mock_run:
        out = orch.handle_input("hai")
        mock_chat.assert_called_once()
        mock_run.assert_not_called()
    assert "hello chat" in out


def test_natural_language_assess_goes_supervisor(orch):
    with patch.object(orch.supervisor, "run", return_value="hello agent") as mock_run:
        out = orch.handle_input("scan example.com")
        mock_run.assert_called_once()
    assert out == "hello agent"


def test_platform_registry_count():
    from wth_ai.platform.registry import get_platform_registry
    reg = get_platform_registry()
    assert len(reg.commands) >= 20


def test_platform_complete():
    from wth_ai.platform.registry import get_platform_registry
    hits = get_platform_registry().complete("/rec")
    assert any("recon" in h for h in hits)
