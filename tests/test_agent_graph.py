"""Agent graph: batas node/kedalaman, timeout, kill, dan persistensi.

Semua test memakai `runner` palsu — tanpa jaringan, tanpa orchestrator, tanpa
LLM. Yang diuji adalah mekanika graf (batas, status, pewarisan parent, kill),
bukan isi agen.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, cast

import pytest

from wth_ai.agents import graph_store
from wth_ai.agents.graph import MAX_DEPTH, MAX_NODES, AgentGraph
from wth_ai.agents.subagent.registry import MAX_DEPTH as REGISTRY_MAX_DEPTH
from wth_ai.killswitch import clear_kill, request_kill


def _fast(node):
    return f"ok:{node.node_id}"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setiap test menulis ke workspace sendiri; tidak pernah menyentuh repo."""
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))
    clear_kill()
    yield
    clear_kill()


def test_max_depth_is_shared_with_registry():
    assert MAX_DEPTH == REGISTRY_MAX_DEPTH
    assert MAX_NODES == 8


def test_spawn_beyond_max_nodes_is_rejected():
    graph = AgentGraph(runner=_fast, persist=False)
    for i in range(MAX_NODES):
        assert graph.spawn("recon", f"task {i}")["ok"] is True

    rejected = graph.spawn("recon", "one too many")

    assert rejected["ok"] is False
    assert str(MAX_NODES) in rejected["error"]
    assert graph.node_count == MAX_NODES
    assert len(graph.nodes()) == MAX_NODES


def test_nesting_beyond_max_depth_is_rejected():
    graph = AgentGraph(runner=_fast, persist=False)
    root = graph.spawn("recon", "root")
    assert root["ok"] is True

    child = graph.spawn("recon", "child", parent_id=root["node_id"])
    assert child["ok"] is True
    assert child["depth"] == 1

    grandchild = graph.spawn("recon", "grandchild", parent_id=child["node_id"])
    assert grandchild["ok"] is False
    assert str(MAX_DEPTH) in grandchild["error"]


def test_unknown_agent_is_rejected():
    graph = AgentGraph(runner=_fast, persist=False)

    rejected = graph.spawn("does-not-exist", "prompt")

    assert rejected["ok"] is False
    assert "unknown agent" in rejected["error"]
    assert graph.node_count == 0


def test_unknown_parent_is_rejected():
    graph = AgentGraph(runner=_fast, persist=False)

    rejected = graph.spawn("recon", "prompt", parent_id="nope-1")

    assert rejected["ok"] is False
    assert "unknown parent" in rejected["error"]


def test_wait_timeout_marks_node_timeout():
    gate = threading.Event()

    def slow(node):
        gate.wait(2.0)
        return "late but fine"

    graph = AgentGraph(runner=slow, persist=False)
    spawned = graph.spawn("recon", "slow task")

    try:
        result = graph.wait(spawned["node_id"], timeout=0.05)
        assert result["ok"] is True
        assert result["status"] == "timeout"
        assert result["node_id"] in result["timed_out"]
    finally:
        gate.set()
        graph.wait(spawned["node_id"], timeout=5.0)


def test_wait_returns_result_of_fast_runner():
    graph = AgentGraph(runner=_fast, persist=False)
    spawned = graph.spawn("recon", "fast task")

    result = graph.wait(spawned["node_id"], timeout=5.0)

    assert result["status"] == "done"
    assert result["result"] == f"ok:{spawned['node_id']}"


def test_wait_without_node_id_covers_every_node():
    graph = AgentGraph(runner=_fast, persist=False)
    first = graph.spawn("recon", "a")
    second = graph.spawn("auth", "b", parent_id=first["node_id"])

    result = graph.wait(timeout=5.0)

    assert result["ok"] is True
    assert set(result["nodes"]) == {first["node_id"], second["node_id"]}
    assert set(result["nodes"].values()) == {"done"}


def test_child_inherits_parent_record_and_tree_nests_it():
    graph = AgentGraph(runner=_fast, persist=False)
    parent = graph.spawn("recon", "parent task")
    child = graph.spawn("auth", "child task", parent_id=parent["node_id"])
    graph.wait(timeout=5.0)

    nodes = {n["node_id"]: n for n in graph.nodes()}
    assert nodes[child["node_id"]]["parent_id"] == parent["node_id"]
    assert nodes[child["node_id"]]["depth"] == nodes[parent["node_id"]]["depth"] + 1

    tree = graph.tree()
    roots = {root["node_id"]: root for root in tree["roots"]}
    assert parent["node_id"] in roots
    assert roots[parent["node_id"]]["children"][0]["node_id"] == child["node_id"]
    assert roots[parent["node_id"]]["children"][0]["depth"] == 1


def test_send_appends_message_and_rejects_unknown_node():
    graph = AgentGraph(runner=_fast, persist=False)
    spawned = graph.spawn("recon", "task")

    assert graph.send(spawned["node_id"], "extra context")["ok"] is True
    assert graph.send("nope-1", "x")["ok"] is False
    nodes = {n["node_id"]: n for n in graph.nodes()}
    assert nodes[spawned["node_id"]]["messages"] == ["extra context"]


def test_kill_all_marks_nodes_killed_and_sets_cancel():
    gate = threading.Event()

    def blocked(node):
        gate.wait(2.0)
        return "never used"

    graph = AgentGraph(runner=blocked, persist=False)
    first = graph.spawn("recon", "a")
    second = graph.spawn("auth", "b", parent_id=first["node_id"])

    try:
        killed = graph.kill_all()
        assert set(killed) == {first["node_id"], second["node_id"]}
        assert graph.cancelled is True
        assert set(graph.wait(timeout=1.0)["nodes"].values()) == {"killed"}
        assert graph.spawn("recon", "after kill")["ok"] is False
    finally:
        gate.set()
        graph.wait(timeout=5.0)


def test_wait_returns_killed_when_kill_flag_exists():
    gate = threading.Event()

    def blocked(node):
        gate.wait(2.0)
        return "never used"

    graph = AgentGraph(runner=blocked, persist=False)
    spawned = graph.spawn("recon", "task")

    try:
        request_kill("test")
        result = graph.wait(spawned["node_id"], timeout=5.0)
        assert result["status"] == "killed"
        assert graph.cancelled is True
    finally:
        gate.set()
        clear_kill()
        graph.wait(spawned["node_id"], timeout=5.0)


def test_graph_persists_and_round_trips(tmp_path: Path):
    graph = AgentGraph(run_id="run-test", runner=_fast)
    parent = graph.spawn("recon", "a")
    child = graph.spawn("auth", "b", parent_id=parent["node_id"])
    graph.wait(timeout=5.0)

    path = graph_store.graph_path("run-test")
    assert path.exists()
    assert path == tmp_path / "home" / "runtime" / "agents" / "run-test" / "graph.json"

    reloaded = graph_store.load_graph("run-test")
    assert reloaded is not None
    recorded = {n["node_id"]: n for n in reloaded["nodes"]}
    assert set(recorded) == {parent["node_id"], child["node_id"]}
    assert recorded[parent["node_id"]]["status"] == "done"
    assert recorded[child["node_id"]]["parent_id"] == parent["node_id"]
    assert graph_store.list_runs() == ["run-test"]


def test_load_missing_graph_returns_none(tmp_path: Path):
    assert graph_store.load_graph("never-written") is None


def test_default_runner_degrades_without_orchestrator():
    graph = AgentGraph(persist=False)
    spawned = graph.spawn("recon", "task")

    result = graph.wait(spawned["node_id"], timeout=5.0)

    assert result["status"] == "done"
    assert "orchestrator" in result["result"]


def test_tools_are_registered_and_dispatch_through_ctx():
    from types import SimpleNamespace

    from wth_ai.agents.supervisor_dispatch import _dispatch_agent_graph
    from wth_ai.agents.tool_registry import TOOL_DEFINITIONS

    for tool in ("agent_spawn", "agent_send", "agent_wait", "agent_tree"):
        assert tool in TOOL_DEFINITIONS

    ctx = cast(Any, SimpleNamespace(orchestrator=None, agent_graph=None))

    spawned = json.loads(_dispatch_agent_graph(ctx, "agent_spawn", {"subagent": "recon", "prompt": "x"}))
    assert spawned["ok"] is True
    # Graf dibuat lazy lalu dipegang ctx supaya node bertahan lintas iterasi tool.
    assert ctx.agent_graph is not None

    waited = json.loads(_dispatch_agent_graph(ctx, "agent_wait", {"node_id": spawned["node_id"], "timeout": 5}))
    assert waited["status"] == "done"

    tree = json.loads(_dispatch_agent_graph(ctx, "agent_tree", {}))
    assert [r["node_id"] for r in tree["roots"]] == [spawned["node_id"]]
