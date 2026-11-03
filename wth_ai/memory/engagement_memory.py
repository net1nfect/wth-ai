"""Engagement memory — knowledge graph for planner/executor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wth_ai.models import AttackSurface, utc_now


class EngagementMemory:
    """Persistent engagement knowledge beyond flat SessionState.

    WU-113 (ADR-004): `assets`/`routes` genuinely overlap with `AssetGraph`
    (the actual duplication ADR-004 calls out for merging). `asset_graph`
    is an optional post-construction reference (see orchestrator.py --
    `AssetGraph` is constructed AFTER `EngagementMemory` in `__init__`
    ordering, so it can't be a required constructor arg without reordering
    unrelated code); when wired, `context_block()` auto-syncs from it first
    so callers no longer need to remember to call `sync_from_graph()`
    themselves for the data to be fresh. `controllers`/`middleware`/
    `trust_boundaries` REMOVED here -- verified dead via full-codebase grep
    before removal: initialized, (de)serialized, but never populated by
    ANY method (`update_from_recon`/`sync_from_graph`/`update_from_executor`
    never touch them) and never read outside this file either.
    """

    def __init__(self, session_id: str, base_dir: Path | None = None, *, asset_graph: Any = None) -> None:
        self.session_id = session_id
        home = base_dir or Path("data/engagements")
        self.path = home / session_id / "memory.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_graph = asset_graph
        self.assets: list[str] = []
        self.routes: list[dict[str, Any]] = []
        self.input_surfaces: list[dict[str, Any]] = []
        self.rejected_hypotheses: list[dict[str, Any]] = []
        self.evidence_gaps: list[dict[str, Any]] = []
        self.iteration: int = 0
        self.tech_profile: str = "generic_web"
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assets = data.get("assets", [])
        self.routes = data.get("routes", [])
        self.input_surfaces = data.get("input_surfaces", [])
        self.rejected_hypotheses = data.get("rejected_hypotheses", [])
        self.evidence_gaps = data.get("evidence_gaps", [])
        self.iteration = data.get("iteration", 0)
        self.tech_profile = data.get("tech_profile", "generic_web")

    def save(self) -> None:
        self.path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "updated_at": utc_now(),
            "assets": self.assets,
            "routes": self.routes,
            "input_surfaces": self.input_surfaces,
            "rejected_hypotheses": self.rejected_hypotheses,
            "evidence_gaps": self.evidence_gaps,
            "iteration": self.iteration,
            "tech_profile": self.tech_profile,
        }

    def update_from_recon(self, surface: AttackSurface) -> None:
        self.assets = list(dict.fromkeys(self.assets + surface.subdomains))
        for url in surface.api_endpoints[:50]:
            self.routes.append({"path": url, "type": "api"})
        for url in surface.urls[:30]:
            if any(k in url.lower() for k in ("login", "register", "upload", "reset", "admin")):
                self.input_surfaces.append({"url": url, "type": self._guess_surface(url)})
        if any("laravel" in str(surface.tech_stack).lower() for _ in [1]):
            self.tech_profile = "laravel"
        elif surface.api_endpoints:
            self.tech_profile = "api"
        self.save()

    def sync_from_graph(self, graph: Any) -> None:
        """Sync asset labels from AssetGraph into engagement memory."""
        from wth_ai.graph.models import NodeType

        labels: list[str] = []
        routes: list[dict[str, Any]] = []
        for node in graph.nodes.values():
            labels.append(node.label)
            if node.type in (NodeType.ENDPOINT, NodeType.HOST):
                routes.append({"path": node.label, "type": node.type.value})
        self.assets = list(dict.fromkeys(self.assets + labels[:100]))
        # WU-113: dedupe by path -- plain extend() previously accumulated a
        # duplicate entry on EVERY repeated sync_from_graph() call (already
        # live pre-existing bug across multiple /hunt invocations in one
        # session; made auto-called from context_block() below, so fixing
        # it here is a prerequisite for that being safe to call repeatedly).
        existing_paths = {r["path"] for r in self.routes}
        for r in routes[:50]:
            if r["path"] not in existing_paths:
                self.routes.append(r)
                existing_paths.add(r["path"])
        self.save()

    def update_from_executor(self, result: dict[str, Any]) -> None:
        for ev in result.get("evidence", []):
            if isinstance(ev, str) and "route" in ev.lower():
                self.routes.append({"path": ev[:200], "source": "executor"})
        self.save()

    def add_rejection(self, finding_id: str, reason: str) -> None:
        self.rejected_hypotheses.append({"id": finding_id, "reason": reason, "at": utc_now()})
        self.save()

    def add_evidence_gap(self, finding_id: str, needed: str) -> None:
        self.evidence_gaps.append({"id": finding_id, "needed": needed, "at": utc_now()})
        self.save()

    def context_block(self) -> str:
        # WU-113 (ADR-004): auto-sync from asset_graph when wired, so
        # assets/routes are always fresh relative to the graph without every
        # caller needing to remember to call sync_from_graph() first --
        # this is the actual "merge" in spirit (EngagementMemory stops being
        # a second, independently-maintainable copy of graph data).
        if self.asset_graph is not None:
            self.sync_from_graph(self.asset_graph)
        parts = [
            f"ITERATION: {self.iteration}",
            f"TECH_PROFILE: {self.tech_profile}",
            f"ASSETS: {self.assets[:30]}",
            f"ROUTES: {len(self.routes)} mapped",
            f"INPUT_SURFACES: {self.input_surfaces[:15]}",
            f"REJECTED: {len(self.rejected_hypotheses)}",
            f"EVIDENCE_GAPS: {len(self.evidence_gaps)}",
        ]
        return "\n".join(parts)

    @staticmethod
    def _guess_surface(url: str) -> str:
        low = url.lower()
        if "login" in low:
            return "form_login"
        if "register" in low:
            return "form_register"
        if "upload" in low:
            return "file_upload"
        if "reset" in low:
            return "password_reset"
        if "/api/" in low:
            return "api_endpoint"
        if re_id := __import__("re").search(r"[?&](id|uid|user)=", low):
            return "parameter_id"
        return "generic"
