"""Tests for continued platform development (recon CLI, adapters, plugins)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _test_mode(monkeypatch):
    monkeypatch.setenv("WTH_TEST_MODE", "1")


def test_external_adapters_at_least_50():
    from wth_ai.recon.tools.adapters import ADAPTERS

    assert len(ADAPTERS) >= 50


def test_supported_external_matches_adapters():
    from wth_ai.recon.tools.adapters import ADAPTERS
    from wth_ai.recon.tools.catalog import SUPPORTED_EXTERNAL

    missing = set(ADAPTERS) - SUPPORTED_EXTERNAL
    assert not missing, f"adapters not in SUPPORTED_EXTERNAL: {sorted(missing)[:10]}"


def test_recon_plan_text():
    from wth_ai.cli.recon_commands import recon_plan_text

    text = recon_plan_text("example.com", "budget")
    assert "example.com" in text
    assert "Selected" in text


def _load_cli():
    import importlib.util
    from pathlib import Path

    # Sumber CLI ada di paket; root `wth-ai-main.py` hanya shim yang menjalankan
    # file ini lewat runpy, jadi `cli` harus diambil dari sini.
    path = Path(__file__).resolve().parents[1] / "wth_ai" / "cli" / "wth-ai-main.py"
    spec = importlib.util.spec_from_file_location("wth_main", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.cli


def test_recon_cli_group_registered():
    runner = CliRunner()
    cli = _load_cli()

    result = runner.invoke(cli, ["recon", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "watch" in result.output
    assert "tools" in result.output


def test_cron_cli_group_registered():
    runner = CliRunner()
    cli = _load_cli()

    result = runner.invoke(cli, ["cron", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "add" in result.output


def test_orchestrator_loads_plugin_slash_handlers():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    # Plugin hooks may register /ctf or /bounty from first-party plugins
    assert hasattr(orch, "slash_router")
    assert isinstance(orch.slash_router.handlers, dict)


def test_whois_parser_extracts_hosts():
    from wth_ai.recon.tools.adapters import _parse_whois

    out = "Domain Name: API.EXAMPLE.COM\nName Server: NS1.EXAMPLE.COM\n"
    items = _parse_whois(out)
    hosts = {i["host"] for i in items if i.get("kind") == "subdomain"}
    assert "api.example.com" in hosts or "ns1.example.com" in hosts
