"""Netguard benar-benar dipasang di jalur HTTP agen, bukan hanya tersedia."""

from unittest.mock import patch

from wth_ai.config import WTHConfig
from wth_ai.models import PermissionMode
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge


def _bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    scope = ScopeManager()
    scope.set_scope(target="172.28.0.10", authorized=True,
                    in_scope=["172.28.0.0/16"])
    bridge = AgentTerminalBridge(WTHConfig.load(), PermissionManager(
        PermissionMode.UNRESTRICTED), scope, "test-session")
    return bridge


def test_bridge_blocks_egress_outside_allowlist(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path, monkeypatch)
    with patch.object(bridge.scope, "is_in_scope", return_value=(True, "ok")), \
         patch("requests.request") as req:
        res = bridge.http_probe("GET", "https://1.1.1.1/x", actor="planner")
    assert res.blocked is True
    assert "egress" in res.block_reason.lower()
    req.assert_not_called()