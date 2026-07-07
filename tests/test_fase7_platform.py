"""Fase 7 platform: plugins, memory md, docker backend, mcp serve."""

from __future__ import annotations

import pytest

from wth_ai.memory.md_context import context_block, write_memory
from wth_ai.plugins.manager import PluginManager
from wth_ai.mcp.serve import McpServeRegistry
from wth_ai.terminal.backends.docker import DockerTerminalBackend
from wth_ai.memory.learning import HuntMemoryLearning, LearningRecord


def test_plugins_load_first_party(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.events import get_event_bus
    from wth_ai.config import WTHConfig
    mgr = PluginManager(WTHConfig.load(), bus=get_event_bus())
    loaded = mgr.load_all()
    assert len(loaded) >= 2


def test_memory_md_context(monkeypatch, tmp_path):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    write_memory("retry SSRF on staging")
    block = context_block()
    assert "staging" in block


def test_mcp_serve_list_tools():
    reg = McpServeRegistry()
    tools = reg.list_tools()
    names = {t["name"] for t in tools}
    assert "graph_export" in names
    assert "recon_plan" in names


def test_docker_backend_interface():
    backend = DockerTerminalBackend()
    assert backend.name == "docker"


def test_learning_nudge(monkeypatch, tmp_path):
    learn = HuntMemoryLearning(db_path=tmp_path / "learn.db")
    learn.record(LearningRecord("php", "sqli", True, ["sqlmap"], endpoint_pattern="/admin"))
    learn.record(LearningRecord("php", "sqli", True, ["manual"], endpoint_pattern="/api"))
    nudge = learn.engagement_nudge("php")
    assert "sqli" in nudge.lower() or nudge == ""
