"""Bot routing via gateway RPC."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wth_ai.bots.base import BotSessionManager
from wth_ai.config import WTHConfig


def test_bot_uses_gateway_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    mgr = BotSessionManager(WTHConfig.load())
    mock_client = MagicMock()
    mock_client.handle_input.return_value = "gateway-response"
    with patch("wth_ai.gateway.client.gateway_available", return_value=True), patch(
        "wth_ai.gateway.client.GatewayClient", return_value=mock_client
    ):
        out = mgr.run_command("telegram", "123", "/help")
    assert out == "gateway-response"
    mock_client.handle_input.assert_called_once_with("/help")


def test_bot_falls_back_local(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    mgr = BotSessionManager(WTHConfig.load())
    orch = mgr.get_or_create("discord", "99")
    orch.run_once = MagicMock(return_value="local-response")  # type: ignore[method-assign]
    with patch("wth_ai.gateway.client.gateway_available", return_value=False):
        mgr._sessions["discord:99"] = orch
        out = mgr.run_command("discord", "99", "/status")
    assert out == "local-response"
