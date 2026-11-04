"""Installer + `wth setup`: preflight, pemasangan, dan setup non-interaktif.

Yang dijaga di sini: preflight tidak menulis apa pun, pemasangan memakai argv
( bukan shell ), setup non-interaktif menyimpan key hanya di vault, dan skrip
install bisa diuji tanpa jaringan lewat `--dry-run`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from wth_ai import config as cfg_mod
from wth_ai import install as inst
from wth_ai.config import WTHConfig
from wth_ai.vault import SecretVault

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #
def test_build_report_is_read_only(tmp_path):
    home = tmp_path / "home"
    report = inst.build_report(home=home)

    assert report.python_ok is True
    assert report.home == home
    assert home.exists() is False
    assert isinstance(report.missing_core, list)


def test_report_lists_missing_core_dependency(tmp_path):
    report = inst.build_report(home=tmp_path)
    # Modul yang pasti tidak ada di lingkungan mana pun.
    assert all(d.module != "definitely_not_a_module" for d in report.missing_core)

    report.missing_core.append(inst.DepStatus("x", "x>=1", False))
    text = inst.format_report(report)
    assert "x>=1" in text
    assert "WAJIB" in text


def test_requirements_for_includes_features_and_missing(monkeypatch, tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = [inst.DepStatus("click", "click>=8.1", False)]

    reqs = inst.requirements_for(["tui"], report)
    assert "click>=8.1" in reqs
    assert any(r.startswith("textual") for r in reqs)


def test_requirements_for_ignores_unknown_feature(tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = []
    assert inst.requirements_for(["tidak-ada"], report) == []


def test_install_missing_dry_run_does_not_spawn(monkeypatch, tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = [inst.DepStatus("click", "click>=8.1", False)]
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))

    code, commands = inst.install_missing(report, dry_run=True)

    assert code == 0
    assert called == []
    assert commands and "click>=8.1" in commands[0]


def test_install_missing_uses_argv_not_shell(tmp_path):
    report = inst.build_report(home=tmp_path)
    report.missing_core = [inst.DepStatus("click", "click>=8.1", False)]
    seen: list[tuple] = []

    class _Proc:
        returncode = 0

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return _Proc()

    code, _ = inst.install_missing(report, runner=fake_run)
    argv, kwargs = seen[0]

    assert code == 0
    assert isinstance(argv, list)
    assert "shell" not in kwargs
    assert argv[:3] == [inst.sys.executable, "-m", "pip"]


def test_pip_argv_adds_user_only_outside_venv(monkeypatch):
    monkeypatch.setattr(inst, "in_virtualenv", lambda: True)
    assert "--user" not in inst.pip_argv(["x"])
    monkeypatch.setattr(inst, "in_virtualenv", lambda: False)
    assert "--user" in inst.pip_argv(["x"], user=True)


def test_apply_setup_creates_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    report, _note = inst.apply_setup(home=tmp_path / "home", smoke=False)

    assert (report.home / "config").is_dir()
    assert (report.home / "data" / "sessions").is_dir()
    assert (report.home / "db.sqlite").exists()


# --------------------------------------------------------------------------- #
# setup non-interaktif
# --------------------------------------------------------------------------- #
@pytest.fixture
def user_cfg(tmp_path, monkeypatch):
    path = tmp_path / "user.yaml"
    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", path)
    return path


def test_build_payload_maps_provider_flags():
    from wth_ai.cli.setup_wizard import build_setup_payload

    payload = build_setup_payload(
        provider="9router",
        base_url="http://127.0.0.1:20128/v1",
        api_key="sk-router-123456",
        model="glm-5",
    )
    assert payload["provider_type"] == "9router"
    assert payload["router9_base_url"] == "http://127.0.0.1:20128/v1"
    assert payload["router9_api_key"] == "sk-router-123456"
    assert payload["model"] == "glm-5"


def test_build_payload_custom_provider_uses_custom_keys():
    from wth_ai.cli.setup_wizard import build_setup_payload

    payload = build_setup_payload(provider="custom", base_url="http://lab:4000/v1", api_key="sk-lab-123456")
    assert payload["custom_base_url"] == "http://lab:4000/v1"
    assert payload["custom_api_key"] == "sk-lab-123456"


def test_build_payload_rejects_unknown_provider():
    from wth_ai.cli.setup_wizard import build_setup_payload

    with pytest.raises(ValueError):
        build_setup_payload(provider="tidak-ada")


def test_non_interactive_setup_writes_vault_ref_only(user_cfg):
    from wth_ai.cli.setup_wizard import build_setup_payload, run_setup_wizard

    payload = build_setup_payload(
        provider="custom",
        base_url="http://lab:4000/v1",
        api_key="sk-never-plaintext-999",
        model="lab-large",
    )
    config = run_setup_wizard(non_interactive=payload)

    assert config.providers["planner"].type == "custom"
    assert config.providers["planner"].model == "lab-large"
    assert config.setup_complete is True

    raw = user_cfg.read_text(encoding="utf-8")
    assert "sk-never-plaintext-999" not in raw
    data = yaml.safe_load(raw)
    assert data["secrets"]["custom_base_url"] == "http://lab:4000/v1"
    assert data["secrets"]["custom_api_key"] == "vault:custom_api_key"
    assert SecretVault().get("custom_api_key") == "sk-never-plaintext-999"


def test_non_interactive_setup_keeps_9router_base_url(user_cfg):
    from wth_ai.cli.setup_wizard import build_setup_payload, run_setup_wizard

    payload = build_setup_payload(
        provider="9router", base_url="http://127.0.0.1:20128/v1", api_key="sk-r9-1234567", model="glm-5",
    )
    run_setup_wizard(non_interactive=payload)

    data = yaml.safe_load(user_cfg.read_text(encoding="utf-8"))
    assert data["secrets"]["router9_base_url"] == "http://127.0.0.1:20128/v1"
    assert data["secrets"]["router9_api_key"] == "vault:router9_api_key"


# --------------------------------------------------------------------------- #
# scripts/install.sh
# --------------------------------------------------------------------------- #
def test_install_script_parses():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash tidak tersedia")
    result = subprocess.run([bash, "-n", str(INSTALL_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
