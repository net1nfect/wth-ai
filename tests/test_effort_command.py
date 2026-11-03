"""`/effort` — level effort low|medium|high|xhigh|ultra.

Gejala yang memicu fitur ini (terukur dari sesi operator): jawaban terpotong di
tengah kalimat / terlalu pendek, sementara `config.models.max_tokens` tidak
punya permukaan operator, dan provider instance di-cache dengan nilai LAMA
(ubah config tanpa reset cache = tidak berefek sama sekali).

Yang dikunci di sini: mapping level -> parameter, deteksi level, pesan sukses,
reset cache provider (provider BARU benar-benar memakai nilai baru), persistence
ke path tmp (test TIDAK menyentuh config asli user), dan wiring registry/router.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")

import pytest  # noqa: E402

from wth_ai.config import WTHConfig  # noqa: E402
from wth_ai.effort import (  # noqa: E402
    EFFORT_LEVELS,
    apply_effort,
    detect_effort,
    effort_names,
)


class _FakeClient:
    def __init__(self) -> None:
        self.resets = 0

    def reset_provider_cache(self) -> None:
        self.resets += 1


class _FakeOrch:
    def __init__(self, config: WTHConfig) -> None:
        self.config = config
        self.client = _FakeClient()


@pytest.fixture(autouse=True)
def _iso_paths(tmp_path, monkeypatch):
    """Jangan pernah menulis ke config asli user dari dalam test."""
    import wth_ai.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", tmp_path / "iso" / "user.yaml", raising=False)
    monkeypatch.setattr(cfg_mod, "USER_ENV_PATH", tmp_path / "iso" / ".env", raising=False)


def _isolated_config(tmp_path: Path) -> WTHConfig:
    cfg = WTHConfig()
    cfg.user_config_path = tmp_path / "iso" / "user.yaml"
    return cfg


# ── kontrak level ────────────────────────────────────────────────────────


def test_levels_are_exactly_the_requested_tiers_and_strictly_increasing():
    assert effort_names() == ["low", "medium", "high", "xhigh", "ultra"]
    tokens = [EFFORT_LEVELS[n]["max_tokens"] for n in effort_names()]
    assert tokens == sorted(tokens)
    assert len(set(tokens)) == len(tokens)


def test_medium_matches_legacy_defaults(tmp_path):
    """Sesi yang tidak pernah pakai /effort harus tetap berperilaku sama."""
    cfg = _isolated_config(tmp_path)
    assert detect_effort(cfg) == "medium"
    assert cfg.models.max_tokens == 8192
    assert cfg.models.planner_timeout_seconds == 90
    assert cfg.models.executor_timeout_seconds == 120


def test_apply_roundtrip_for_every_level(tmp_path):
    cfg = _isolated_config(tmp_path)
    for level in effort_names():
        spec = apply_effort(cfg, level)
        assert cfg.models.max_tokens == spec["max_tokens"]
        assert cfg.models.planner_timeout_seconds == spec["planner_timeout_seconds"]
        assert detect_effort(cfg) == level


def test_unknown_level_raises_before_touching_config(tmp_path):
    cfg = _isolated_config(tmp_path)
    with pytest.raises(KeyError):
        apply_effort(cfg, "ludicrous")
    assert cfg.models.max_tokens == 8192


# ── handler ──────────────────────────────────────────────────────────────


def test_handler_sets_level_reports_success_resets_cache_persists(tmp_path):
    from wth_ai.cli.handlers.effort_handler import handle_effort

    cfg = _isolated_config(tmp_path)
    orch = _FakeOrch(cfg)
    out = handle_effort(orch, ["xhigh"])
    assert out.startswith("\u2713 effort \u2192 xhigh")
    assert "max_tokens 32768" in out
    assert "tersimpan" in out
    assert "cache provider di-reset" in out
    assert cfg.models.max_tokens == 32768
    assert orch.client.resets == 1
    assert cfg.user_config_path.exists()  # persistence nyata ke path tmp


def test_handler_rejects_unknown_level_with_choices(tmp_path):
    from wth_ai.cli.handlers.effort_handler import handle_effort

    out = handle_effort(_FakeOrch(_isolated_config(tmp_path)), ["ludicrous"])
    assert "tidak dikenal" in out
    assert "ultra" in out and "low" in out


def test_bare_effort_without_tty_lists_options_and_current(tmp_path):
    from wth_ai.cli.handlers.effort_handler import handle_effort

    out = handle_effort(_FakeOrch(_isolated_config(tmp_path)), [])
    for name in effort_names():
        assert name in out
    assert "Effort sekarang" in out


def test_handler_accepts_mixed_case(tmp_path):
    from wth_ai.cli.handlers.effort_handler import handle_effort

    cfg = _isolated_config(tmp_path)
    out = handle_effort(_FakeOrch(cfg), ["ULTRA"])
    assert out.startswith("\u2713 effort \u2192 ultra")
    assert cfg.models.max_tokens == 65536


# ── provider cache (akar "ubah config tidak berefek") ────────────────────


def test_provider_cache_reset_applies_new_params(tmp_path):
    from wth_ai.llm.router import LLMProviderRouter

    cfg = _isolated_config(tmp_path)
    cfg.secrets.custom_api_key = "test-key"
    router = LLMProviderRouter(cfg)
    p1 = router._get_or_create_provider("custom")
    assert p1.max_tokens == 8192

    apply_effort(cfg, "high")
    router.reset_provider_cache()
    p2 = router._get_or_create_provider("custom")
    assert p2 is not p1
    assert p2.max_tokens == 16384


# ── wiring ───────────────────────────────────────────────────────────────


def test_wired_in_registry_and_slash_router():
    from unittest.mock import MagicMock

    from wth_ai.cli.slash_router import build_slash_router
    from wth_ai.platform.registry import get_platform_registry

    assert "/effort" in get_platform_registry().all_slash_names()
    router = build_slash_router(MagicMock())
    assert "/effort" in router.handlers


def test_orchestrator_end_to_end(tmp_path, monkeypatch):
    import wth_ai.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", tmp_path / "iso" / "user.yaml", raising=False)
    monkeypatch.setattr(cfg_mod, "USER_ENV_PATH", tmp_path / "iso" / ".env", raising=False)
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))

    from wth_ai.orchestrator import WTHOrchestrator

    cfg = WTHConfig()
    cfg.user_config_path = tmp_path / "iso" / "user.yaml"
    orch = WTHOrchestrator(cfg, fresh=True)
    out = orch.handle_input("/effort ultra")
    assert "\u2713 effort \u2192 ultra" in out
    assert cfg.models.max_tokens == 65536
    assert orch.client._providers == {}
