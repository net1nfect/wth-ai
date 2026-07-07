"""Tests for engagement mode and unrestricted exploration."""

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.engagement.mode import EngagementMode, is_unrestricted, set_engagement_mode, set_unrestricted
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.profiles import builtin_presets, list_profiles


def test_unrestricted_bypasses_command_check():
    pm = PermissionManager(PermissionMode.UNRESTRICTED)
    result = pm.check_command("sqlmap -u http://example.com --batch")
    assert result["allowed"] is True
    set_unrestricted(False)


def test_ctf_and_bb_profiles_exist():
    presets = builtin_presets()
    assert "ctf-aggressive" in presets
    assert "bug-bounty" in presets
    assert presets["ctf-aggressive"]["safety"]["unrestricted"] is True
    assert "9router" in presets["ctf-aggressive"]["models"]["planner"]["provider"]


def test_engagement_mode_set():
    set_engagement_mode(EngagementMode.CTF)
    from wth_ai.engagement.mode import get_engagement_mode
    assert get_engagement_mode() == EngagementMode.CTF


def test_profiles_list_includes_new():
    names = list_profiles()
    assert "ctf-aggressive" in names
    assert "bug-bounty" in names
