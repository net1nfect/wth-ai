"""Dashboard data provider + Rich snapshot renderer (headless-safe)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Cyberpunk neon palette.
NEON_CYAN = "#22d3ee"
NEON_PURPLE = "#a855f7"
NEON_GREEN = "#10b981"
NEON_AMBER = "#f59e0b"
NEON_RED = "#ef4444"
DIM = "#64748b"

_SEV_COLOR = {
    "critical": NEON_RED, "high": NEON_RED, "medium": NEON_AMBER,
    "low": NEON_GREEN, "info": DIM,
}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass
class DashboardData:
    """Aggregates live platform state for the TUI (read-only, never raises)."""

    manager: Any = None

    def _mgr(self) -> Any:
        if self.manager is None:
            from wth_ai.jobs.manager import JobManager
            self.manager = JobManager()
        return self.manager

    # ----------------------------------------------------------------- #
    def jobs(self, limit: int = 8) -> list[Any]:
        try:
            return self._mgr().store.list_jobs(limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def latest_job_dir(self) -> Path | None:
        try:
            store = self._mgr().store
            jobs = store.list_jobs(limit=50)
            for j in jobs:
                d = Path(store.results_dir) / j.job_id
                if (d / "normalized").exists() or (d / "triage_findings.json").exists():
                    return d
        except Exception:  # noqa: BLE001
            return None
        return None

    def recon_status(self) -> dict[str, Any]:
        d = self.latest_job_dir()
        summary = _load(d / "recon_summary.json") if d else None
        if not summary:
            return {}
        return {"target": summary.get("target", "?"), "assets": summary.get("assets", {}),
                "priority_targets": summary.get("priority_targets", 0)}

    def asset_graph(self) -> dict[str, Any]:
        d = self.latest_job_dir()
        g = _load(d / "normalized" / "asset_graph.json") if d else None
        return g or {}

    def risk_heatmap(self) -> dict[str, list]:
        d = self.latest_job_dir()
        return (_load(d / "normalized" / "risk_heatmap.json") if d else None) or {}

    def findings(self) -> list[dict[str, Any]]:
        d = self.latest_job_dir()
        tri = _load(d / "triage_findings.json") if d else None
        return tri.get("findings", []) if isinstance(tri, dict) else []

    def workers(self) -> dict[str, Any]:
        try:
            return self._mgr().summary()
        except Exception:  # noqa: BLE001
            return {}

    def approvals(self) -> list[Any]:
        try:
            from wth_ai.approvals import ApprovalRegistry
            return ApprovalRegistry().list_pending()
        except Exception:  # noqa: BLE001
            return []

    def audit_tail(self, n: int = 8) -> list[str]:
        try:
            from wth_ai.workspace import wth_home
            path = wth_home() / "logs" / "audit.log"
            if not path.exists():
                return []
            return path.read_text(encoding="utf-8").splitlines()[-n:]
        except Exception:  # noqa: BLE001
            return []


# --------------------------------------------------------------------------- #
# Rich snapshot
# --------------------------------------------------------------------------- #
def _recon_panel(data: DashboardData) -> Panel:
    s = data.recon_status()
    body = Text()
    if not s:
        body.append("no recon runs yet\n", style=DIM)
    else:
        body.append(f"target  {s['target']}\n", style=NEON_CYAN)
        for k, v in (s.get("assets") or {}).items():
            body.append(f"  {k:<16}", style=DIM)
            body.append(f"{v}\n", style=NEON_GREEN)
        body.append(f"priority targets  {s.get('priority_targets', 0)}\n", style=NEON_PURPLE)
    return Panel(body, title="[b]RECON STATUS[/b]", border_style=NEON_CYAN)


def _graph_panel(data: DashboardData) -> Panel:
    g = data.asset_graph()
    body = Text()
    body.append(f"nodes {g.get('node_count', 0)}  edges {g.get('edge_count', 0)}\n",
                style=NEON_GREEN)
    for ntype, count in list((g.get("node_types") or {}).items())[:10]:
        body.append(f"  {ntype:<14}", style=DIM)
        body.append(f"{count}\n", style=NEON_CYAN)
    return Panel(body, title="[b]ASSET GRAPH[/b]", border_style=NEON_GREEN)


def _heatmap_panel(data: DashboardData) -> Panel:
    heat = data.risk_heatmap()
    body = Text()
    for tier in ("critical", "high", "medium", "low", "info"):
        items = heat.get(tier, [])
        bar = "█" * min(len(items), 20)
        body.append(f"{tier:<9}", style=_SEV_COLOR.get(tier, DIM))
        body.append(f"{bar} {len(items)}\n", style=_SEV_COLOR.get(tier, DIM))
    return Panel(body, title="[b]RISK HEATMAP[/b]", border_style=NEON_AMBER)


def _worker_panel(data: DashboardData) -> Panel:
    w = data.workers()
    body = Text()
    body.append(f"jobs total  {w.get('total', 0)}\n", style=NEON_CYAN)
    body.append(f"queued      {w.get('queued', 0)}\n", style=NEON_AMBER)
    for status, n in (w.get("by_status") or {}).items():
        body.append(f"  {status:<14}", style=DIM)
        body.append(f"{n}\n", style=NEON_GREEN)
    return Panel(body, title="[b]WORKER MONITOR[/b]", border_style=NEON_PURPLE)


def _evidence_panel(data: DashboardData) -> Panel:
    findings = data.findings()
    t = Table.grid(padding=(0, 1))
    t.add_column(style=NEON_AMBER)
    t.add_column()
    if not findings:
        t.add_row("", Text("no findings yet", style=DIM))
    for f in findings[:8]:
        sev = f.get("severity", "info")
        t.add_row(Text(sev.upper(), style=_SEV_COLOR.get(sev, DIM)),
                  Text(f.get("title", "")[:48], style="white"))
    return Panel(t, title="[b]EVIDENCE STREAM[/b]", border_style=NEON_CYAN)


def _approval_panel(data: DashboardData) -> Panel:
    pend = data.approvals()
    body = Text()
    if not pend:
        body.append("no pending approvals\n", style=DIM)
    else:
        for a in pend[:6]:
            aid = getattr(a, "approval_id", getattr(a, "id", "?"))
            body.append(f"⏵ {aid}\n", style=NEON_AMBER)
    return Panel(body, title="[b]APPROVAL CONSOLE[/b]", border_style=NEON_RED)


def _audit_panel(data: DashboardData) -> Panel:
    body = Text()
    tail = data.audit_tail()
    if not tail:
        body.append("audit log empty\n", style=DIM)
    for line in tail:
        body.append(line[:70] + "\n", style=DIM)
    return Panel(body, title="[b]AUDIT TIMELINE[/b]", border_style=DIM)


def render_snapshot(data: DashboardData | None = None) -> Group:
    """Return a Rich renderable dashboard snapshot (headless-safe)."""
    data = data or DashboardData()
    header = Text("  W T H · A I   —   R E A L T I M E   R E C O N   P L A T F O R M  ",
                  style=f"bold {NEON_PURPLE} on #0b1020")
    top = Table.grid(expand=True)
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    top.add_column(ratio=1)
    top.add_row(_recon_panel(data), _graph_panel(data), _heatmap_panel(data))
    mid = Table.grid(expand=True)
    mid.add_column(ratio=1)
    mid.add_column(ratio=1)
    mid.add_row(_worker_panel(data), _evidence_panel(data))
    bottom = Table.grid(expand=True)
    bottom.add_column(ratio=1)
    bottom.add_column(ratio=1)
    bottom.add_row(_approval_panel(data), _audit_panel(data))
    return Group(header, top, mid, bottom)
