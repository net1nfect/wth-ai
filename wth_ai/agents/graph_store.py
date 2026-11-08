"""Persistensi state `AgentGraph` sebagai JSON di workspace operator.

Path memakai `wth_home()` yang membaca env `WTH_HOME`, sehingga test dengan
monkeypatch tidak pernah menulis ke repo atau ke home asli.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wth_ai.workspace import atomic_write_text, wth_home


def agents_root() -> Path:
    return wth_home() / "runtime" / "agents"


def graph_dir(run_id: str) -> Path:
    return agents_root() / run_id


def graph_path(run_id: str) -> Path:
    return graph_dir(run_id) / "graph.json"


def save_graph(run_id: str, snapshot: dict[str, Any]) -> Path:
    """Tulis atomik (`<path>.tmp` + `os.replace`) supaya pembaca tidak pernah
    melihat JSON terpotong saat proses mati di tengah tulis."""
    payload = json.dumps(snapshot, indent=2, sort_keys=True)
    return atomic_write_text(graph_path(run_id), payload + "\n")


def load_graph(run_id: str) -> dict[str, Any] | None:
    """State terakhir, atau None kalau run ini belum pernah ditulis."""
    path = graph_path(run_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def list_runs() -> list[str]:
    """Run id yang punya graph.json, terbaru lebih dulu."""
    root = agents_root()
    if not root.exists():
        return []
    runs = [p.parent.name for p in root.glob("*/graph.json") if p.parent.is_dir()]
    return sorted(runs, reverse=True)
