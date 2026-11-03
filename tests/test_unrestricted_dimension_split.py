"""Regression tests — WU-003: pecah WTH_UNRESTRICTED jadi 3 dimensi independen.

ADR-012 / AUTHZ-01 (SECURITY_AUDIT.md): sebelumnya WTH_UNRESTRICTED adalah SATU
flag yang membypass scope-authorization + capability-ceiling + human-approval
sekaligus di 6+ titik. Sekarang: scope_authorization_required() (dimensi 1),
is_unrestricted() (dimensi 2 -- TIDAK berubah nama/perilaku SENDIRI, cuma
dipersempit maknanya), auto_approve_enabled() (dimensi 3) -- ketiganya
independen, default AMAN (dimensi 1 True/wajib, dimensi 3 False/wajib approve).

Test kunci di file ini membuktikan DEKOPLING-nya: mengaktifkan dimensi 2
(capability unrestricted) TIDAK LAGI membuat dimensi 1/3 ikut permisif.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import WTHConfig
from wth_ai.engagement.mode import (
    auto_approve_enabled,
    is_unrestricted,
    scope_authorization_required,
    set_unrestricted,
)


# ---------------------------------------------------------------------------
# Default aman per dimensi (fresh state, tidak ada env override)
# ---------------------------------------------------------------------------

def test_scope_authorization_required_default_is_true(monkeypatch):
    monkeypatch.delenv("WTH_REQUIRE_AUTHORIZED", raising=False)
    assert scope_authorization_required(WTHConfig()) is True


def test_auto_approve_enabled_default_is_false(monkeypatch):
    monkeypatch.delenv("WTH_AUTO_APPROVE", raising=False)
    assert auto_approve_enabled(WTHConfig()) is False


def test_safety_config_require_authorized_default_true():
    cfg = WTHConfig()
    assert cfg.safety.require_authorized is True


def test_agent_config_auto_approve_default_false():
    cfg = WTHConfig()
    assert cfg.agent.auto_approve_unrestricted is False


# ---------------------------------------------------------------------------
# Env var override per dimensi -- independen satu sama lain
# ---------------------------------------------------------------------------

def test_require_authorized_env_override(monkeypatch):
    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "0")
    assert scope_authorization_required() is False
    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "1")
    assert scope_authorization_required() is True


def test_auto_approve_env_override(monkeypatch):
    monkeypatch.setenv("WTH_AUTO_APPROVE", "1")
    assert auto_approve_enabled() is True
    monkeypatch.setenv("WTH_AUTO_APPROVE", "0")
    assert auto_approve_enabled() is False


# ---------------------------------------------------------------------------
# BUKTI DEKOPLING -- inti WU-003. Mengaktifkan dimensi 2 (capability) TIDAK
# LAGI membuat dimensi 1/3 ikut permisif (beda dari perilaku lama).
# ---------------------------------------------------------------------------

def test_capability_unrestricted_does_not_bypass_authorization(monkeypatch):
    monkeypatch.delenv("WTH_REQUIRE_AUTHORIZED", raising=False)
    monkeypatch.setenv("WTH_UNRESTRICTED", "1")
    try:
        assert is_unrestricted() is True, "prasyarat: dimensi 2 harus aktif untuk test ini"
        assert scope_authorization_required() is True, (
            "REGRESI WU-003: dimensi 2 (capability) aktif tidak boleh membuat "
            "dimensi 1 (authorization) ikut permisif"
        )
    finally:
        monkeypatch.setenv("WTH_UNRESTRICTED", "0")


def test_capability_unrestricted_does_not_bypass_approval(monkeypatch):
    monkeypatch.delenv("WTH_AUTO_APPROVE", raising=False)
    monkeypatch.setenv("WTH_UNRESTRICTED", "1")
    try:
        assert is_unrestricted() is True, "prasyarat: dimensi 2 harus aktif untuk test ini"
        assert auto_approve_enabled() is False, (
            "REGRESI WU-003: dimensi 2 (capability) aktif tidak boleh membuat "
            "dimensi 3 (approval) ikut auto-approve"
        )
    finally:
        monkeypatch.setenv("WTH_UNRESTRICTED", "0")


# ---------------------------------------------------------------------------
# Integration: ReconEngine benar-benar blocked meski capability unrestricted
# ---------------------------------------------------------------------------

def test_recon_engine_blocks_unauthorized_target_even_with_capability_unrestricted(monkeypatch, tmp_path):
    from wth_ai.recon.engine import ReconEngine

    monkeypatch.delenv("WTH_REQUIRE_AUTHORIZED", raising=False)
    monkeypatch.setenv("WTH_UNRESTRICTED", "1")
    try:
        engine = ReconEngine(results_root=tmp_path)
        result = engine.run("example.com", authorized=False)
        assert result["status"] == "blocked", (
            f"REGRESI WU-003 (AUTHZ-01): recon terhadap target unauthorized lolos "
            f"hanya karena capability ceiling unrestricted. result={result}"
        )
    finally:
        monkeypatch.setenv("WTH_UNRESTRICTED", "0")


def test_recon_engine_allows_when_authorization_dimension_explicitly_disabled(monkeypatch, tmp_path):
    """Positive/compatibility: dimensi 1 MASIH bisa dinonaktifkan eksplisit
    (mis. lab lokal) -- fix ini TIDAK menghapus kemampuan itu, hanya memisahkannya
    dari dimensi 2."""
    from wth_ai.recon.engine import ReconEngine

    monkeypatch.setenv("WTH_REQUIRE_AUTHORIZED", "0")
    try:
        engine = ReconEngine(results_root=tmp_path)
        result = engine.run("example.com", authorized=False)
        assert result["status"] != "blocked" or result.get("reason") != "authorized=no (recon-only requires authorization)"
    finally:
        monkeypatch.delenv("WTH_REQUIRE_AUTHORIZED", raising=False)


# ---------------------------------------------------------------------------
# profiles.py -- preset tidak lagi bisa set require_authorized=False
# ---------------------------------------------------------------------------

def test_presets_no_longer_set_require_authorized_false():
    from wth_ai.profiles import builtin_presets

    presets = builtin_presets()
    for name in ("bug-bounty", "ctf-aggressive"):
        safety = presets[name]["safety"]
        assert "require_authorized" not in safety, (
            f"Preset {name!r} masih bisa set require_authorized -- "
            "dimensi 1 harus jadi keputusan per-engagement manusia, bukan template"
        )


def test_presets_still_set_capability_unrestricted():
    """Regression guard existing behavior (tests/test_engagement_mode.py::
    test_ctf_and_bb_profiles_exist) -- dimensi 2 TETAP bisa diset preset, cuma
    tidak lagi ikut membypass dimensi lain."""
    from wth_ai.profiles import builtin_presets

    presets = builtin_presets()
    assert presets["ctf-aggressive"]["safety"]["unrestricted"] is True
    assert presets["bug-bounty"]["safety"]["unrestricted"] is True
