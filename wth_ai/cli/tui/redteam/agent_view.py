"""Panel agen read-only: memformat snapshot graf, tidak memutasi apa pun.

Mutasi graf (spawn/send/wait) hanya lewat tool supervisor, di mana `MAX_NODES`
dan `MAX_DEPTH` ditegakkan. Panel ini sengaja tidak punya jalan ke sana: ia
membaca `AgentGraph.tree()` atau snapshot terakhir di `graph_store`.
"""

from __future__ import annotations

from typing import Any

from wth_ai.agents import graph_store

MAX_ROWS = 8
HEADER = "Agen (read-only)"

def _duration(value: Any) -> str:
    try:
        seconds = float(value or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    return f"{seconds:.1f}s"

def _walk(node: dict[str, Any], *, indent: int = 0, rows: list[str], limit: int) -> None:
    if len(rows) >= limit:
        return
    rows.append(
        f"{'  ' * indent}{node.get('node_id', '?')} · {node.get('agent_id', '?')} · "
        f"{node.get('status', '?')} · {_duration(node.get('duration_s'))}"
    )
    for child in node.get("children") or []:
        _walk(child, indent=indent + 1, rows=rows, limit=limit)

def tree_from_flat(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Snapshot `graph_store` menyimpan node datar; panel butuh pohon.

    Node dengan `parent_id` yang tidak ada diperlakukan sebagai root supaya
    snapshot setengah jadi tetap bisa ditampilkan.
    """
    by_id = {str(n.get("node_id")): dict(n) for n in nodes if n.get("node_id")}
    roots: list[dict[str, Any]] = []
    for node_id, node in by_id.items():
        node.setdefault("children", [])
        parent = node.get("parent_id")
        if parent and str(parent) in by_id and str(parent) != node_id:
            by_id[str(parent)]["children"].append(node)
        else:
            roots.append(node)
    return {"roots": roots}

def agent_panel(tree: dict[str, Any], *, limit: int = MAX_ROWS) -> str:
    """Teks panel dari struktur `AgentGraph.tree()` (atau `tree_from_flat`)."""
    roots = tree.get("roots") or []
    run = tree.get("run_id")
    title = f"{HEADER} · run {run}" if run else HEADER
    rows: list[str] = []
    for root in roots:
        _walk(root, rows=rows, limit=limit)
    if not rows:
        return f"{title}\n  belum ada node"
    total = _count(tree)
    if total > len(rows):
        rows.append(f"  … {total - len(rows)} node lain tidak ditampilkan")
    return "\n".join([title, *rows])

def _count(tree: dict[str, Any]) -> int:
    def _n(node: dict[str, Any]) -> int:
        return 1 + sum(_n(child) for child in node.get("children") or [])

    return sum(_n(root) for root in tree.get("roots") or [])

def agent_summary(run_id: str | None = None, *, limit: int = MAX_ROWS) -> str:
    """Panel untuk run tersimpan terbaru (atau `run_id` tertentu)."""
    if run_id is None:
        runs = graph_store.list_runs()
        if not runs:
            return f"{HEADER}\n  belum ada run tersimpan di $WTH_HOME/runtime/agents"
        run_id = runs[0]

    snapshot = graph_store.load_graph(run_id)
    if snapshot is None:
        return f"{HEADER} · run {run_id}\n  snapshot tidak terbaca (JSON rusak atau belum ditulis)"

    tree = snapshot if "roots" in snapshot else tree_from_flat(snapshot.get("nodes") or [])
    tree.setdefault("run_id", run_id)
    return agent_panel(tree, limit=limit)
