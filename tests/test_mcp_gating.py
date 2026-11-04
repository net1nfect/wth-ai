"""MCP: mode digerbangi sebelum transport, argumen diredaksi di audit."""

import json

from wth_ai.config import WTHConfig
from wth_ai.mcp.manager import MCPClientManager
from wth_ai.modes import BY_KEY


def _manager(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    cfg = WTHConfig.load()
    cfg.mcp.servers = {"stub": {"command": "true", "args": []}}
    return MCPClientManager(cfg)


def test_mode_without_mcp_capability_is_refused(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    scribe = BY_KEY["scribe"]
    assert "mcp" in {cap.value for cap in scribe.caps}

    plan = BY_KEY["plan"].__class__(
        key="no-mcp", label="no-mcp", glyph="×", color="#000000", autonomy=0,
        caps=frozenset(), asks=True, stops_early=True, blurb="",
    )
    result = manager.call_tool("stub", "echo", {"message": "hi"}, mode=plan)
    assert result.success is False
    assert "kapabilitas" in result.error


def test_audit_redacts_secret_arguments(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    manager.call_tool("stub", "echo", {"token": "supersecretvalue123"}, mode=None)
    log = (tmp_path / "logs" / "mcp_audit.jsonl").read_text()
    assert "supersecretvalue123" not in log
    entry = json.loads(log.splitlines()[-1])
    assert entry["server"] == "stub"