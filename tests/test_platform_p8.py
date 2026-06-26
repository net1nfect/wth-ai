"""Fase 6 P8 tests: plugin registry, recovery controller, TUI dashboard."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")

import pytest


# --------------------------------------------------------------------------- #
# Plugin registry
# --------------------------------------------------------------------------- #
def test_plugin_register_and_run():
    from wth_ai.plugins.registry import PLUGIN_CATEGORIES, PluginRegistry

    reg = PluginRegistry()
    assert set(PLUGIN_CATEGORIES) == {
        "recon", "executor", "scoring", "analyzer", "visual", "reporting"}
    reg.register("scoring", "double", lambda x: x * 2, description="double")
    assert reg.count() == 1
    out = reg.run_category("scoring", 21)
    assert out["double"] == 42


def test_plugin_unknown_category():
    from wth_ai.plugins.registry import PluginRegistry
    with pytest.raises(ValueError):
        PluginRegistry().register("bogus", "x", lambda: None)


def test_plugin_failure_isolated():
    from wth_ai.plugins.registry import PluginRegistry
    reg = PluginRegistry()

    def boom():
        raise RuntimeError("nope")

    reg.register("analyzer", "boom", boom)
    reg.register("analyzer", "ok", lambda: "fine")
    out = reg.run_category("analyzer")
    assert out["ok"] == "fine"
    assert "error" in out["boom"]


def test_plugin_discovery(tmp_path):
    from wth_ai.plugins.registry import PluginRegistry, get_plugin_registry

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "myplugin.py").write_text(
        "from wth_ai.plugins import register_plugin\n"
        "@register_plugin('recon', 'hello')\n"
        "def hello():\n    return 'hi'\n",
        encoding="utf-8",
    )
    loaded = PluginRegistry().discover(plugins_dir)
    assert loaded == 1
    # the decorator registered on the *global* registry
    assert get_plugin_registry().get("recon", "hello") is not None


# --------------------------------------------------------------------------- #
# Recovery controller
# --------------------------------------------------------------------------- #
def test_classify_error_taxonomy():
    from wth_ai.recovery import ERROR_TYPES, classify_error

    assert classify_error("Target is out of scope") == "scope_violation"
    assert classify_error("HTTP 429 too many requests") == "rate_limit_exceeded"
    assert classify_error("connection timed out") == "network_timeout"
    assert classify_error("disk full: no space left") == "storage_failure"
    assert classify_error("totally weird") == "state_anomaly"
    assert all(t in ERROR_TYPES for t in
               ("scope_violation", "rate_limit_exceeded", "worker_crash"))


def test_recovery_controller_enters_recovery():
    from wth_ai.events import EventBus
    from wth_ai.recovery import RecoveryController

    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.type))
    logs = []
    ctrl = RecoveryController(audit_log=logs.append, bus=bus)
    d = ctrl.handle("Out of scope request blocked", context="executor")
    assert d.error_type == "scope_violation"
    assert d.enter_recovery is True
    assert ctrl.in_recovery is True
    assert logs and "recovery" in logs[0]
    assert "app.error" in seen


def test_recovery_controller_transient_no_recovery():
    from wth_ai.recovery import RecoveryController
    d = RecoveryController().handle("network timed out")
    assert d.error_type == "network_timeout"
    assert d.enter_recovery is False
    assert d.action == "retry_or_continue"


# --------------------------------------------------------------------------- #
# TUI dashboard snapshot (headless)
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.workspace import init_workspace
    init_workspace()
    return tmp_path


def test_render_snapshot_headless(tmp_home):
    from rich.console import Console
    from wth_ai.cli.tui import DashboardData, render_snapshot

    snap = render_snapshot(DashboardData())
    console = Console(record=True, width=120)
    console.print(snap)
    text = console.export_text()
    assert "RECON STATUS" in text
    assert "ASSET GRAPH" in text
    assert "RISK HEATMAP" in text
    assert "WORKER MONITOR" in text
    assert "EVIDENCE STREAM" in text
    assert "APPROVAL CONSOLE" in text
    assert "AUDIT TIMELINE" in text


def test_dashboard_data_robust_without_runs(tmp_home):
    from wth_ai.cli.tui import DashboardData
    d = DashboardData()
    assert d.recon_status() == {}
    assert d.findings() == []
    assert isinstance(d.workers(), dict)
    assert d.audit_tail() == [] or isinstance(d.audit_tail(), list)
