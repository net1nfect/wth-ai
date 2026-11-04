"""Bypass terpasang di jalur runtime: config → system prompt → meta balasan."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wth_ai import config as cfg_mod
from wth_ai.agents import bypass, load_bypass_block, load_prompt
from wth_ai.config import WTHConfig


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """WTH_HOME + direktori bypass yang terpisah dari repo."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("WTH_HOME", str(home))
    monkeypatch.setenv("WTH_BYPASS_DIR", str(home / "bypass"))
    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", tmp_path / "user.yaml")
    monkeypatch.delenv("WTH_BYPASS", raising=False)
    return home


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def test_bypass_off_returns_empty_block(isolated):
    assert load_bypass_block(WTHConfig()) == ""


def test_bypass_default_adds_session_block(isolated):
    config = WTHConfig()
    config.agent.bypass = "default"
    block = load_bypass_block(config)
    assert "SESSION INSTRUCTIONS (bypass: wth)" in block
    assert "Operating Policy" in block


def test_missing_profile_is_reported_inside_prompt(isolated):
    config = WTHConfig()
    config.agent.bypass = "tidak-ada"
    block = load_bypass_block(config)
    assert "gagal dimuat" in block
    assert "TANPA bypass" in block


def test_load_prompt_appends_bypass_after_constitution(tmp_path, isolated):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "chat_system.txt").write_text("PROMPT ASLI", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("KONSTITUSI", encoding="utf-8")

    config = WTHConfig()
    config.prompts_dir = str(prompts)
    config.agent.bypass = "default"

    text = load_prompt(config, "chat_system.txt")
    assert text.index("KONSTITUSI") < text.index("PROMPT ASLI") < text.index("SESSION INSTRUCTIONS")


def test_load_prompt_unchanged_without_bypass(tmp_path, isolated):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "chat_system.txt").write_text("PROMPT ASLI", encoding="utf-8")

    config = WTHConfig()
    config.prompts_dir = str(prompts)
    text = load_prompt(config, "chat_system.txt")
    assert "SESSION INSTRUCTIONS" not in text


# --------------------------------------------------------------------------- #
# config: env, simpan, muat
# --------------------------------------------------------------------------- #
def test_env_overrides_agent_bypass(isolated, monkeypatch):
    monkeypatch.setenv("WTH_BYPASS", "lab")
    config = WTHConfig.load()
    assert config.agent.bypass == "lab"


def test_save_persists_bypass_and_reloads(isolated):
    config = WTHConfig()
    config.agent.bypass = "lab"
    config.save_user_config()

    assert WTHConfig.load().agent.bypass == "lab"


def test_save_with_bypass_cleared_does_not_resurrect_it(isolated):
    config = WTHConfig()
    config.agent.bypass = "lab"
    config.save_user_config()

    fresh = WTHConfig.load()
    fresh.agent.bypass = ""
    fresh.save_user_config()

    assert WTHConfig.load().agent.bypass == ""


# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #
def _orch():
    from types import SimpleNamespace

    config = WTHConfig()
    agents = {
        "chat": SimpleNamespace(system_prompt=""),
        "supervisor": SimpleNamespace(system_prompt=""),
    }
    return SimpleNamespace(config=config, **agents)


def test_handler_enable_and_disable(isolated):
    from wth_ai.cli.handlers.bypass_handler import handle_bypass

    orch = _orch()
    out = handle_bypass(orch, ["on"])
    assert "bypass aktif" in out
    assert orch.config.agent.bypass == "default"
    assert "SESSION INSTRUCTIONS" in orch.chat.system_prompt

    out = handle_bypass(orch, ["off"])
    assert "dimatikan" in out
    assert orch.config.agent.bypass == ""
    assert "SESSION INSTRUCTIONS" not in orch.chat.system_prompt


def test_handler_does_not_touch_environment(isolated, monkeypatch):
    """Status bypass hidup di config; env hanya jalur flag CLI."""
    from wth_ai.cli.handlers.bypass_handler import handle_bypass

    monkeypatch.delenv("WTH_BYPASS", raising=False)
    orch = _orch()
    handle_bypass(orch, ["on"])
    assert os.environ.get("WTH_BYPASS") is None

    monkeypatch.setenv("WTH_BYPASS", "dari-luar")
    handle_bypass(orch, ["off"])
    assert os.environ.get("WTH_BYPASS") == "dari-luar"


def test_orchestrator_label_matches_prompt_actually_loaded(isolated, monkeypatch):
    """Label hanya boleh menyebut bypass yang benar-benar masuk system prompt."""
    from wth_ai.orchestrator import WTHOrchestrator

    monkeypatch.setenv("WTH_BYPASS", "kimi-k3-agents")  # env tanpa config
    config = WTHConfig()
    label = WTHOrchestrator._resolve_bypass(config)
    block = load_bypass_block(config)

    assert label == ""
    assert block == ""

    config.agent.bypass = "default"
    assert WTHOrchestrator._resolve_bypass(config) == "wth"


def test_handler_install_then_show_and_use(isolated, tmp_path):
    from wth_ai.cli.handlers.bypass_handler import handle_bypass

    source = tmp_path / "glm-zcode-5.3-AGENTS.md"
    source.write_text("# Workspace\nReal mechanism, not a simulation.", encoding="utf-8")

    orch = _orch()
    assert "terpasang" in handle_bypass(orch, ["install", str(source)])
    assert "glm-zcode-5.3-agents" in handle_bypass(orch, ["list"])
    assert "Real mechanism" in handle_bypass(orch, ["show", "glm-zcode-5.3-agents"])
    assert "bypass aktif" in handle_bypass(orch, ["on", "glm-zcode-5.3-agents"])


def test_handler_status_reports_off_by_default(isolated):
    from wth_ai.cli.handlers.bypass_handler import handle_bypass

    out = handle_bypass(_orch(), [])
    assert "bypass: mati" in out
    assert "wth" in out  # profil bawaan terdaftar


def test_platform_registry_exposes_bypass():
    from wth_ai.platform.registry import get_platform_registry

    assert "/bypass" in get_platform_registry().all_slash_names()


def test_slash_router_registers_bypass():
    from wth_ai.cli.slash_router import build_slash_router
    from types import SimpleNamespace

    config = WTHConfig()
    orch = SimpleNamespace(
        config=config,
        client=None,
        **{name: (lambda *_a, **_k: "") for name in (
            "_handle_permission", "_handle_computer", "_handle_research", "_handle_jobs",
            "_handle_eval", "_handle_usage", "_handle_next", "_handle_payloads",
            "_handle_autopilot", "_handle_eval_act", "_assess_loop", "_run_hunt",
            "_execute_next", "_verify", "_report", "_handle_approve", "_run_intel",
            "_show_skill", "_browser_probe", "_handle_scope", "_status_summary",
            "_list_sessions", "resume", "new_session", "request_quit", "supervisor",
            "doctor", "findings_db", "asset_graph", "mcp", "state",
        )},
    )
    assert "/bypass" in build_slash_router(orch).handlers