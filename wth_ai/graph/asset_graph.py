"""Attack Surface Graph — hosts, endpoints, params, roles, edges."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wth_ai.graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from wth_ai.models import AttackSurface, utc_now


HIGH_VALUE_PARAMS = frozenset({
    "id", "user_id", "uid", "uuid", "account", "order_id", "file", "path",
    "url", "redirect", "next", "return", "callback", "token", "key", "email",
    "role", "admin", "debug", "test",
})


class AssetGraph:
    """Persistent graph of attack surface relationships."""

    def __init__(self, session_id: str, base_dir: Path | None = None) -> None:
        home = base_dir or Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai")) / "data" / "engagements"
        self.path = home / session_id / "graph.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._index: dict[str, str] = {}  # label key -> node id
        self._load()

    def _key(self, ntype: NodeType, label: str) -> str:
        return f"{ntype.value}:{label.lower()}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.nodes = {n["id"]: GraphNode.from_dict(n) for n in raw.get("nodes", [])}
            self.edges = [GraphEdge.from_dict(e) for e in raw.get("edges", [])]
            self._rebuild_index()
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    def _rebuild_index(self) -> None:
        self._index = {self._key(n.type, n.label): n.id for n in self.nodes.values()}

    def save(self) -> None:
        self.path.write_text(
            json.dumps({
                "updated_at": utc_now(),
                "nodes": [n.to_dict() for n in self.nodes.values()],
                "edges": [e.to_dict() for e in self.edges],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_node(self, node: GraphNode) -> GraphNode:
        key = self._key(node.type, node.label)
        if key in self._index:
            existing = self.nodes[self._index[key]]
            existing.data.update(node.data)
            existing.priority = max(existing.priority, node.priority)
            return existing
        self.nodes[node.id] = node
        self._index[key] = node.id
        return node

    def add_edge(self, source: str, target: str, etype: EdgeType, **data: Any) -> None:
        for e in self.edges:
            if e.source == source and e.target == target and e.type == etype:
                e.data.update(data)
                return
        self.edges.append(GraphEdge(source=source, target=target, type=etype, data=data))

    def add_from_recon(self, surface: AttackSurface) -> None:
        target = surface.target
        if target:
            host = self.add_node(GraphNode.create(NodeType.HOST, target, url=f"https://{target}"))

        for sub in surface.subdomains:
            h = self.add_node(GraphNode.create(NodeType.HOST, sub))
            if target:
                self.add_edge(h.id, host.id, EdgeType.HOSTED_ON)

        for tech, ver in surface.tech_stack.items():
            self.add_node(GraphNode.create(NodeType.TECH_STACK, f"{tech}:{ver}", tech=tech, version=ver))

        for url in list(dict.fromkeys(surface.urls + surface.api_endpoints))[:500]:
            ep = self.add_node(GraphNode.create(
                NodeType.ENDPOINT, url,
                method="GET",
                auth_required="/api/" in url or "/admin" in url,
            ))
            parsed = urlparse(url)
            if parsed.hostname:
                hk = self._key(NodeType.HOST, parsed.hostname)
                if hk in self._index:
                    self.add_edge(ep.id, self._index[hk], EdgeType.HOSTED_ON)

        for p in surface.parameters:
            score = _param_priority(p.param)
            param = self.add_node(GraphNode.create(
                NodeType.PARAMETER, f"{p.url}?{p.param}",
                url=p.url, param=p.param, method=p.method, score=score,
                priority=score,
            ))
            ep_key = self._key(NodeType.ENDPOINT, p.url)
            if ep_key in self._index:
                self.add_edge(param.id, self._index[ep_key], EdgeType.PARAM_OF)

        for js in surface.js_files[:100]:
            self.add_node(GraphNode.create(NodeType.JS_BUNDLE, js, url=js))

        for s in surface.secrets:
            self.add_node(GraphNode.create(
                NodeType.SECRET, s.snippet[:80] or s.source,
                source=s.source, secret_type=s.secret_type,
            ))

        self.save()

    def add_auth_flow(self, url: str, flow_type: str) -> GraphNode:
        return self.add_node(GraphNode.create(
            NodeType.AUTH_FLOW, url, flow_type=flow_type, priority=8,
        ))

    def add_role(self, name: str) -> GraphNode:
        return self.add_node(GraphNode.create(NodeType.AUTH_ROLE, name, role=name))

    def query_high_value_params(self, limit: int = 30) -> list[GraphNode]:
        params = [n for n in self.nodes.values() if n.type == NodeType.PARAMETER]
        params.sort(key=lambda n: (-n.priority, n.label))
        return params[:limit]

    def endpoints_for_role(self, role: str) -> list[GraphNode]:
        role_ids = {n.id for n in self.nodes.values() if n.type == NodeType.AUTH_ROLE and role in n.label}
        out: list[GraphNode] = []
        for e in self.edges:
            if e.type == EdgeType.REQUIRES_AUTH and e.source in role_ids:
                if e.target in self.nodes:
                    out.append(self.nodes[e.target])
        return out

    def mark_tested(self, node_id: str) -> None:
        if node_id in self.nodes:
            self.nodes[node_id].tested = True

    def coverage(self) -> dict[str, Any]:
        testable = [n for n in self.nodes.values() if n.type in {NodeType.ENDPOINT, NodeType.PARAMETER}]
        tested = sum(1 for n in testable if n.tested)
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "testable": len(testable),
            "tested": tested,
            "coverage_pct": round(100 * tested / len(testable), 1) if testable else 0.0,
        }

    def export_for_planner(self, limit: int = 60) -> str:
        cov = self.coverage()
        lines = [
            "=== ASSET GRAPH ===",
            f"Nodes: {cov['nodes']} | Edges: {cov['edges']} | Coverage: {cov['coverage_pct']}%",
            "",
            "HIGH-VALUE PARAMETERS:",
        ]
        for p in self.query_high_value_params(15):
            d = p.data
            lines.append(f"- [{p.priority}] {d.get('param')} @ {d.get('url')} (tested={p.tested})")
        lines.append("")
        lines.append("API/AUTH ENDPOINTS:")
        for n in sorted(
            [x for x in self.nodes.values() if x.type == NodeType.ENDPOINT],
            key=lambda x: -x.priority,
        )[:20]:
            auth = n.data.get("auth_required", False)
            lines.append(f"- {n.label} auth={auth}")
        auth_flows = [n for n in self.nodes.values() if n.type == NodeType.AUTH_FLOW]
        if auth_flows:
            lines.append("")
            lines.append("AUTH FLOWS:")
            for f in auth_flows[:8]:
                lines.append(f"- {f.data.get('flow_type', 'auth')}: {f.label}")
        lines.append("=== END GRAPH ===")
        return "\n".join(lines[:limit + 20])

    def format_summary(self) -> str:
        cov = self.coverage()
        by_type: dict[str, int] = {}
        for n in self.nodes.values():
            by_type[n.type.value] = by_type.get(n.type.value, 0) + 1
        lines = [
            "## Asset Graph",
            f"- Nodes: {cov['nodes']} | Edges: {cov['edges']}",
            f"- Coverage: {cov['tested']}/{cov['testable']} ({cov['coverage_pct']}%)",
            "",
        ]
        for t, c in sorted(by_type.items()):
            lines.append(f"- {t}: {c}")
        lines.append("")
        lines.append("### Top params")
        for p in self.query_high_value_params(8):
            d = p.data
            lines.append(f"- `{d.get('param')}` @ {d.get('url', '')[:60]}")
        return "\n".join(lines)


def _param_priority(param: str) -> int:
    p = param.lower()
    if p in HIGH_VALUE_PARAMS:
        return 9
    if any(k in p for k in ("id", "user", "token", "redirect", "url", "file", "path")):
        return 7
    if any(k in p for k in ("q", "search", "query", "page")):
        return 4
    return 3
