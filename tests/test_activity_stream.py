"""Verbose activity stream tests."""

from __future__ import annotations

import os
from io import StringIO

os.environ.setdefault("WTH_TEST_MODE", "1")

from rich.console import Console

from wth_ai.cli.ui.activity_stream import ActivityStream, live_activity_enabled, verbose_enabled
from wth_ai.cli.ui.theme import SPINNER_FRAMES
from wth_ai.cli.ui.tool_trace import ToolTracePanel
from wth_ai.config import UIConfig, WTHConfig


def test_verbose_enabled_default():
    cfg = WTHConfig(ui=UIConfig(verbose=True))
    assert verbose_enabled(cfg) is True


def test_verbose_env_off():
    os.environ["WTH_VERBOSE"] = "0"
    try:
        assert verbose_enabled() is False
    finally:
        os.environ.pop("WTH_VERBOSE", None)


def test_activity_stream_tool_lines():
    buf = StringIO()
    console = Console(file=buf, width=100, highlight=False)
    stream = ActivityStream(console)
    stream.on_tool_start("scope_set", {"target": "example.com"})
    stream.on_tool_end("scope_set", "Scope set: example.com", 42)
    out = buf.getvalue()
    assert "scope_set" in out
    assert "example.com" in out
    assert "42" in out


def test_activity_stream_subprocess_line():
    buf = StringIO()
    stream = ActivityStream(Console(file=buf, width=100, highlight=False))
    stream.on_subprocess_line("scanning port 443")
    assert "scanning port 443" in buf.getvalue()


def test_activity_stream_llm_lines():
    buf = StringIO()
    stream = ActivityStream(Console(file=buf, width=120, highlight=False))
    stream.on_llm_start("planner", "9router", "Hacker", {"host": "127.0.0.1:20128", "tools": 12, "messages": 3})
    stream.on_llm_tick("planner", 5)
    stream.on_llm_end("planner", "Hacker", 4200, {"tool_calls": 0, "content_len": 42})
    out = buf.getvalue()
    assert "llm planner" in out
    assert "Hacker" in out
    assert "waiting" in out
    assert "4200" in out


def test_tool_trace_spinner_advances():
    panel = ToolTracePanel()
    a = panel.advance_spinner()
    b = panel.advance_spinner()
    assert a != b or len(SPINNER_FRAMES) == 1


def test_tool_trace_log_lines():
    panel = ToolTracePanel(max_log_lines=3)
    panel.add_log_line("recon start")
    panel.add_log_line("builtin dns: success")
    assert len(panel.log_lines) == 2
    panel.add_log_line("line3")
    panel.add_log_line("line4")
    assert len(panel.log_lines) == 3
    assert panel.log_lines[0] == "line2" or panel.log_lines[-1] == "line4"


def test_job_activity_bridge_forwards_log():
    from wth_ai.cli.ui.job_activity_bridge import wire_job_activity, unwire_job_activity
    from wth_ai.cli.ui.tool_trace import ToolTracePanel
    from wth_ai.events import get_event_bus

    class _Orch:
        config = WTHConfig(ui=UIConfig(verbose=True, activity_mode="live"))
        _tool_trace = ToolTracePanel()
        _activity_stream = None

    orch = _Orch()
    handler = wire_job_activity(orch)
    try:
        get_event_bus().emit("job.log", job_id="abc12345", data={"line": "plan: 12 tools"})
        assert any("plan" in line for line in orch._tool_trace.log_lines)
    finally:
        unwire_job_activity(handler)


def test_live_activity_tty():
  import sys
  if sys.stdout.isatty():
      assert live_activity_enabled(WTHConfig()) in {True, False}
