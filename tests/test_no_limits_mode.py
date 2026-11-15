"""WTH_NO_LIMITS / godmode: master switch behavior and choke-point bypasses."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.engagement.mode import (
    auto_approve_enabled,
    is_no_limits,
    is_unrestricted,
    safety_status,
    scope_authorization_required,
    set_no_limits,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("WTH_NO_LIMITS", "0")
    from wth_ai.engagement import mode as mode_mod

    monkeypatch.setattr(mode_mod, "_no_limits", False)
    monkeypatch.setattr(mode_mod, "_unrestricted", False)
    monkeypatch.setenv("WTH_UNRESTRICTED", "0")
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "1")
    yield


def test_master_switch_forces_all_dimensions():
    assert not is_no_limits()
    set_no_limits(True)
    assert is_no_limits()
    assert is_unrestricted()
    assert auto_approve_enabled()
    assert not scope_authorization_required()
    assert safety_status()["no_limits"] is True


def test_master_switch_off_clears_itself():
    set_no_limits(True)
    set_no_limits(False)
    assert not is_no_limits()
    # disabling the master does not silently rewrite the explicit dimensions
    assert is_unrestricted()


def test_scope_manager_bypasses_scope_and_authorization():
    from wth_ai.scope_manager import ScopeManager

    mgr = ScopeManager()
    assert mgr.is_in_scope("nobody.example")[0] is False
    assert mgr.validate_authorized()[0] is False
    set_no_limits(True)
    assert mgr.is_in_scope("nobody.example")[0] is True
    assert mgr.validate_authorized()[0] is True


def test_policy_allows_denylisted_and_unknown_commands():
    from wth_ai.models import PermissionMode
    from wth_ai.permission_manager import PermissionManager

    pm = PermissionManager(PermissionMode.READ_ONLY_RECON)
    assert pm.check_command("rm -rf /")["allowed"] is False
    assert pm.check_command("totally-unknown-tool --x")["allowed"] is False
    set_no_limits(True)
    assert pm.check_command("rm -rf /")["allowed"] is True
    assert pm.check_command("totally-unknown-tool --x")["allowed"] is True
    assert pm.mode_sufficient(PermissionMode.UNRESTRICTED) is True
    assert pm.permission_check_block(PermissionMode.UNRESTRICTED, "critical")["approval_needed"] is False


def test_redaction_is_raw_under_no_limits():
    from wth_ai.redaction import mask_url_secrets, redact_secrets

    text = "Authorization: Bearer abcdef0123456789"
    url = "https://x.test/?api_key=SECRET"
    assert "REDACTED" in redact_secrets(text)
    assert "REDACTED" in mask_url_secrets(url)
    set_no_limits(True)
    assert redact_secrets(text) == text
    assert mask_url_secrets(url) == url


def test_apply_preset_godmode_and_back_does_not_leak():
    from wth_ai.permission_presets import apply_preset

    apply_preset("godmode")
    assert is_no_limits() and not scope_authorization_required()
    apply_preset("ask")
    assert not is_no_limits()
    assert scope_authorization_required()
    assert not is_unrestricted()


def test_tool_registry_exposes_all_tools_under_no_limits():
    from wth_ai.agents.tool_registry import ACTOR_TOOLS, tools_for_actor

    before = {t["function"]["name"] for t in tools_for_actor("planner")}
    set_no_limits(True)
    after = {t["function"]["name"] for t in tools_for_actor("planner")}
    assert len(after) > len(before)
    assert "run_shell" in after and "malware_forge" in after
    assert after >= set(ACTOR_TOOLS["planner"])


def test_load_prompt_replaces_constitution_and_adds_malware_doctrine():
    from wth_ai.agents import load_prompt

    cfg = WTHConfig()
    normal = load_prompt(cfg, "supervisor_system.txt")
    assert "NO-LIMITS / GODMODE OVERRIDE" not in normal
    set_no_limits(True)
    prompt = load_prompt(cfg, "supervisor_system.txt")
    assert "NO-LIMITS / GODMODE OVERRIDE" in prompt
    assert "MALWARE FORGE MODE" in prompt


def test_agent_loop_sets_unlimited_iterations():
    from wth_ai.agents.tool_loop import AgentToolLoop

    class _Client:
        pass

    set_no_limits(True)
    loop = AgentToolLoop(_Client(), None, max_iters=1)  # type: ignore[arg-type]
    assert loop.max_iters == 0


def test_mass_payload_is_auto_runnable_under_no_limits():
    from wth_ai.hunt.generator import validate_generated

    payload, err = validate_generated({
        "vuln_class": "idor_write",
        "title": "mass update",
        "proves": "semua objek berubah",
        "probe": {"method": "PATCH", "url": "https://x.test/api/1"},
        "blast_radius": "mass",
        "cleanup": "revert",
    })
    assert err == "" and payload is not None
    assert payload.auto_runnable is False
    set_no_limits(True)
    payload, err = validate_generated({
        "vuln_class": "idor_write",
        "title": "mass update",
        "proves": "semua objek berubah",
        "probe": {"method": "PATCH", "url": "https://x.test/api/1"},
        "blast_radius": "mass",
        "cleanup": "revert",
    })
    assert err == "" and payload is not None
    assert payload.auto_runnable is True
