"""Tests for /quit shutdown."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("WTH_TEST_MODE", "1")
os.environ.setdefault("WTH_PLAIN", "1")
os.environ.setdefault("WTH_SKIP_SETUP", "1")

from wth_ai.cli.quit import SessionQuit
from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


def test_quit_raises_session_quit():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    with pytest.raises(SessionQuit) as exc:
        orch.handle_input("/quit")
    assert "ditutup" in exc.value.message.lower() or "WTH-AI" in exc.value.message


def test_shutdown_stops_supervisor_and_persists():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    orch.supervisor.stop = MagicMock(return_value="stopped")
    with patch.object(orch, "_persist") as persist:
        msg = orch.shutdown()
    orch.supervisor.stop.assert_called_once()
    persist.assert_called()
    assert "ditutup" in msg.lower() or "WTH-AI" in msg


def test_quit_not_routed_to_supervisor():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    with patch.object(orch.supervisor, "run") as mock_run:
        with pytest.raises(SessionQuit):
            orch.handle_input("/quit")
        mock_run.assert_not_called()


def test_gateway_router_catches_session_quit():
    from wth_ai.gateway.router import GatewayRouter

    orch = MagicMock()
    orch.handle_input.side_effect = SessionQuit("bye gateway")
    router = GatewayRouter(cache=MagicMock(get=lambda *a, **k: orch))
    assert router.handle("cli:default", "/quit") == "bye gateway"


def test_help_lists_quit():
    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    out = orch.handle_input("/help")
    assert "/quit" in out
