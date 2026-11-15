"""Proactive autonomy state and foreground cycle tests."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from wth_ai.models import Scope


class _Result:
    status_code = 200
    response_headers = {"Content-Type": "text/html"}
    stdout = "<html>ok</html>"
    analysis = ""
    transcript_id = "TX-AUTO"
    stderr = ""


def test_state_roundtrip_update_and_log(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.continuous import state as st

    initial = st.AutonomyState(targets=["example.com"], interval=30, steps=2)
    st.write_state(initial)
    loaded = st.read_state()
    assert loaded.targets == ["example.com"]
    assert loaded.interval == 30
    assert loaded.steps == 2
    assert (tmp_path / "runtime" / "autonomy.json").exists()

    updated = st.update_state(cycles=2, steps_run=7, adaptive_steps=3, adaptive_wins=1)
    assert updated.cycles == 2
    assert updated.steps_run == 7
    assert st.read_state().adaptive_wins == 1

    st.log_line("unit-test line")
    assert "unit-test line" in st.read_state().log[-1]
    assert "unit-test line" in (tmp_path / "runtime" / "autonomy.log").read_text()

    st.write_state(st.AutonomyState(targets=["x"], pid=0))
    assert not (tmp_path / "runtime" / "autonomy.pid").exists()


def test_pid_alive_current_and_dead():
    from wth_ai.continuous import state as st

    assert st.pid_alive(os.getpid()) is True
    assert st.pid_alive(0) is False
    assert st.pid_alive(999_999_999) is False


def test_format_status_reports_targets_and_counters(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.continuous import control, state as st

    st.write_state(st.AutonomyState(targets=["10.0.0.5"], interval=60, steps=4,
                                    running=False, cycles=3, steps_run=9,
                                    adaptive_steps=2, adaptive_wins=1))
    text = control.format_status(control.status())
    assert "Autonomy" in text
    assert "10.0.0.5" in text
    assert "cycles: 3" in text
    assert "adaptive: 2 (1 win)" in text


def test_run_cycle_uses_bridge_and_is_finite(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.continuous.runner import run_cycle

    calls: list[dict] = []

    def dispatch(name, payload, **kwargs):
        calls.append(payload)
        return _Result()

    orch = SimpleNamespace(
        terminal=SimpleNamespace(dispatch_tool=dispatch),
        config=SimpleNamespace(safety=SimpleNamespace(no_limits=False)),
        state=SimpleNamespace(
            scope=Scope(target=""),
            metadata={},
            _findings_source=lambda: [],
        ),
    )
    summary = run_cycle(orch, ["127.0.0.1:9"], steps=1, cycles=1, adaptive=True)
    assert "127.0.0.1:9" in summary["targets"]
    assert summary["targets"]["127.0.0.1:9"]["steps"] == 1
    assert len(calls) == 1
    assert calls[0]["url"].startswith("http")
    assert orch.state.scope.target == ""


def test_control_start_rejects_empty_targets(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.continuous import control, state as st

    st.write_state(st.AutonomyState(targets=[]))
    try:
        control.start(interval=10, steps=1)
    except ValueError as exc:
        assert "target" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")


def test_slash_autonomy_status(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator.__new__(WTHOrchestrator)
    orch.state = SimpleNamespace(scope=Scope(target=""), metadata={})
    text = orch._handle_autonomy(["status"])
    assert "Autonomy" in text


def test_slash_autonomy_add_and_rm(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator.__new__(WTHOrchestrator)
    orch.state = SimpleNamespace(scope=Scope(target=""), metadata={})
    assert "example.com" in orch._handle_autonomy(["add", "example.com"])
    assert "example.com" not in orch._handle_autonomy(["rm", "example.com"]).split("targets:")[1]


def test_tui_ops_panel_reads_both_subsystems(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    import asyncio

    from textual.widgets import TabbedContent

    from wth_ai.cli.tui.app import WthTuiApp
    from wth_ai.continuous import state as st

    st.write_state(st.AutonomyState(targets=["ops.example"], interval=45, steps=2,
                                    running=False, cycles=4, steps_run=8))

    orch = MagicMock()
    orch.config.ui.max_trace_lines = 20
    orch.config.ui.mode = "tui"
    orch.client.available.return_value = True
    orch.state.scope.target = "example.com"
    orch.state.session_id = "abc12345"
    orch.state.findings = []
    orch.state.metadata = {
        "adaptive_last": {
            "target": "example.com", "baseline_status": 403,
            "baseline_signal": "blocked", "vendor": "cloudflare",
            "rounds": 1, "bypassed": True, "best_strategy": "header_xff",
            "stopped_reason": "bypass via header_xff",
            "attempts": [{"round": 1, "strategy": "header_xff", "status": 200,
                          "signal": "success", "improved": True}],
        }
    }
    orch._ui_bus = None

    async def _boot():
        app = WthTuiApp(orch)
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()
            app.query_one("#main-tabs", TabbedContent).active = "tab-ops"
            await pilot.pause()
            return app.query_one("#ops-pane").render()

    text = asyncio.run(_boot())
    rendered = getattr(text, "plain", None) or str(text)
    assert "Ops Dashboard" in rendered
    assert "adaptive" in rendered.lower()
    assert "ops.example" in rendered
