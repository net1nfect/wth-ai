"""Panel agen TUI: read-only, tanpa tombol yang memutasi graf.

`agent_view` hanya memformat snapshot yang sudah ada (`AgentGraph.tree()` atau
`graph_store`). Tidak ada spawn/kill dari panel — mutasi tetap lewat tool
supervisor, di mana batas node/kedalaman ditegakkan.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("textual")

from wth_ai.agents import graph_store  # noqa: E402
from wth_ai.cli.tui.redteam.agent_view import agent_panel, agent_summary  # noqa: E402

_TREE: dict[str, Any] = {
    "run_id": "run-1",
    "roots": [
        {
            "node_id": "n1",
            "agent_id": "recon",
            "status": "done",
            "duration_s": 1.234,
            "depth": 0,
            "children": [
                {
                    "node_id": "n2",
                    "agent_id": "auth",
                    "status": "running",
                    "duration_s": 0.5,
                    "depth": 1,
                    "children": [],
                },
                {
                    "node_id": "n3",
                    "agent_id": "scribe",
                    "status": "timeout",
                    "duration_s": 60.0,
                    "depth": 1,
                    "children": [],
                },
            ],
        }
    ],
}

@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path / "home"))


def test_panel_renders_one_row_per_node_with_status_and_duration():
    text = agent_panel(_TREE)

    lines = [line for line in text.splitlines() if line.strip()]
    # tiga node + header
    assert len([line for line in lines if "n1" in line or "n2" in line or "n3" in line]) == 3
    assert "recon" in text and "done" in text
    assert "running" in text and "timeout" in text
    assert "1.2s" in text  # durasi manusiawi, bukan 1.234


def test_panel_is_read_only():
    import wth_ai.cli.tui.redteam.agent_view as agent_view_module

    for forbidden in ("spawn", "kill", "send", "cancel"):
        assert not hasattr(agent_view_module, forbidden)
    assert "read-only" in agent_panel(_TREE).lower()


def test_empty_tree_says_so_instead_of_crashing():
    text = agent_panel({"roots": []})
    assert "belum ada" in text


def test_summary_reads_latest_persisted_run(tmp_path):
    graph_store.save_graph(
        "run-x",
        {
            "run_id": "run-x",
            "nodes": [
                {"node_id": "n1", "agent_id": "recon", "status": "done", "duration_s": 2.0, "parent_id": None, "depth": 0},
                {"node_id": "n2", "agent_id": "auth", "status": "killed", "duration_s": 0.1, "parent_id": "n1", "depth": 1},
            ],
        },
    )

    text = agent_summary()
    assert "run-x" in text
    assert "n2" in text and "killed" in text
    # nested: anak ditandai indentasi supaya pohon terlihat
    child_line = next(line for line in text.splitlines() if "n2" in line)
    assert child_line.startswith("  ")


def test_summary_without_runs_is_honest():
    assert "belum ada" in agent_summary().lower()


def test_summary_reports_corrupt_snapshot_without_crashing(tmp_path):
    path = graph_store.graph_path("run-bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "run-bad"})[:-1], encoding="utf-8")

    text = agent_summary()
    assert "tidak terbaca" in text or "belum ada" in text
