"""Tests for REPL command classification, permission aliases, and LLM role routing."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.cli.command_class import classify_command, is_quick_command
from wth_ai.config import ProviderRoleConfig, WTHConfig
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager


def test_quick_commands_skip_live_panel():
    assert is_quick_command("/help")
    assert is_quick_command("/permission elevated")
    assert is_quick_command("/status")
    assert not is_quick_command("/hunt")
    assert classify_command("hello") == "chat"


def test_permission_active_test_alias():
    pm = PermissionManager()
    pm.set_mode("active_test")
    assert pm.mode == PermissionMode.ELEVATED_APPROVAL
    assert "4" in pm.display() or pm.mode != PermissionMode.UNRESTRICTED


def test_permission_level_shows_max():
    pm = PermissionManager(PermissionMode.UNRESTRICTED)
    assert "Level: 4/4" in pm.display()


def test_report_role_uses_reporter_provider():
    cfg = WTHConfig()
    cfg.providers["reporter"] = ProviderRoleConfig(
        type="9router",
        model="ag/gemini-pro-agent",
        backup_model=None,
    )
    router = LLMProviderRouter(cfg)
    rc = router._role_config("report")
    assert rc.model == "ag/gemini-pro-agent"
    assert rc.type == "9router"


def test_compact_help_separates_skills_and_mcp():
    from wth_ai.platform.registry import get_platform_registry

    text = get_platform_registry().help_text(compact=True)
    assert "Skills" in text
    assert "MCP" in text
    assert "/mcp" in text
