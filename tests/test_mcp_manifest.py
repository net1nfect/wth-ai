"""`mcp.json` sebagai manifest tambahan; config YAML tetap menang."""

import json

from wth_ai.config import WTHConfig
from wth_ai.mcp.config import load_manifest, merge_manifest, parse_mcp_servers
from wth_ai.mcp.manager import MCPClientManager


def test_manifest_with_one_server(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"stub": {
        "command": "python", "args": ["-m", "stub"], "env": {"A": "1"}}}}))
    servers = load_manifest(path)
    assert set(servers) == {"stub"}
    assert servers["stub"].command == "python"
    assert servers["stub"].args == ["-m", "stub"]
    assert servers["stub"].env == {"A": "1"}


def test_manifest_missing_returns_empty(tmp_path):
    assert load_manifest(tmp_path / "tidak-ada.json") == {}
    assert load_manifest(None) == {}


def test_manifest_broken_json_is_ignored(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{ bukan json")
    assert load_manifest(path) == {}


def test_manifest_accepts_claude_style_key(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"x": {"command": "echo"}}}))
    assert set(load_manifest(path)) == {"x"}


def test_yaml_config_wins_over_manifest():
    manifest = parse_mcp_servers({"same": {"command": "from-manifest"},
                                  "extra": {"command": "manifest-only"}})
    merged = merge_manifest({"same": {"command": "from-yaml"}}, manifest)
    assert merged["same"] == {"command": "from-yaml"}
    assert merged["extra"].command == "manifest-only"


def test_manager_picks_manifest_from_env(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"stub": {"command": "true"}}}))
    monkeypatch.setenv("WTH_MCP_MANIFEST", str(path))
    manager = MCPClientManager(WTHConfig.load())
    assert "stub" in manager.servers
    assert manager.servers["stub"].command == "true"


def test_manager_without_manifest_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_MCP_MANIFEST", str(tmp_path / "nope.json"))
    manager = MCPClientManager(WTHConfig.load())
    assert "stub" not in manager.servers