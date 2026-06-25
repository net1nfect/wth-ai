"""Asset graph builder (Fase 6, flowchart Stage 3).

Builds a typed attack-surface graph from normalized recon data with 13 node
types and 12 edge types, plus a traversal index, host clustering, and simple
risk propagation. Pure-Python, deterministic, no external graph dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from wth_ai.recon.model import PriorityTarget
    from wth_ai.recon.normalizer import ReconNormalizer

NODE_TYPES: tuple[str, ...] = (
    "domain", "subdomain", "ip", "url", "endpoint", "parameter", "api_route",
    "auth_surface", "role", "upload_surface", "websocket", "third_party", "evidence",
)
EDGE_TYPES: tuple[str, ...] = (
    "resolves_to", "redirects_to", "hosts", "exposes", "depends_on",
    "reachable_from", "accepts_parameter", "uses_auth", "contains_route",
    "linked_to_evidence", "shares_certificate", "served_by",
)

# endpoint.kind -> graph node type
_ENDPOINT_NODE = {
    "api": "api_route", "api_schema": "api_route", "graphql": "api_route",
    "auth": "auth_surface", "upload": "upload_surface", "websocket": "websocket",
    "spa_route": "endpoint", "docs": "api_route", "web": "endpoint",
}


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class AssetGraphBuilder:
    def __init__(self, target: str = "") -> None:
        from wth_ai.recon import http as rhttp

        self.root = rhttp.host_of(target) if target else ""
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, str]] = []
        self._edge_keys: set[tuple[str, str, str]] = set()

    # ----------------------------------------------------------------- #
    def _node(self, node_id: str, ntype: str, **attrs: Any) -> str:
        if not node_id:
            return node_id
        existing = self._nodes.get(node_id)
        if existing is None:
            self._nodes[node_id] = {"id": node_id, "type": ntype, "risk": 0.0, **attrs}
        else:
            existing.update({k: v for k, v in attrs.items() if v not in (None, "", 0)})
        return node_id

    def _edge(self, src: str, dst: str, etype: str) -> None:
        if not src or not dst or etype not in EDGE_TYPES:
            return
        key = (src, dst, etype)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self._edges.append({"from": src, "to": dst, "type": etype})

    # ----------------------------------------------------------------- #
    def build(self, norm: "ReconNormalizer", priorities: "list[PriorityTarget] | None" = None) -> dict[str, Any]:
        if self.root:
            self._node(self.root, "domain")

        # subdomains
        for sub in norm.subdomains.values():
            ntype = "subdomain" if (self.root and sub.host != self.root) else "domain"
            self._node(sub.host, ntype, confidence=round(sub.confidence, 3))
            if self.root and sub.host != self.root and sub.host.endswith(self.root):
                self._edge(self.root, sub.host, "hosts")

        # dns records -> ip + resolves_to
        for rec in norm.dns_records.values():
            if rec.record_type in ("A", "AAAA"):
                self._node(rec.value, "ip", record=rec.record_type)
                self._edge(rec.host, rec.value, "resolves_to")

        # urls -> exposes / reachable_from
        for u in norm.urls.values():
            self._node(u.url, "url")
            host = _host_of(u.url)
            if host:
                self._node(host, "subdomain" if host != self.root else "domain")
                self._edge(host, u.url, "exposes")

        # http probes -> served_by (CDN/server), redirects
        for probe in norm.http_probes.values():
            self._node(probe.url, "url", status=probe.status, title=probe.title)
            if probe.server:
                self._node(f"tech:{probe.server}", "third_party", technology=probe.server)
                self._edge(probe.url, f"tech:{probe.server}", "served_by")
            if probe.status in (301, 302):
                # redirect target unknown; mark a self-redirect placeholder edge
                self._edge(probe.url, probe.url, "redirects_to")

        # endpoints -> typed node + contains_route / uses_auth
        # (distinct id so an endpoint is not collapsed into a plain URL node)
        for ep in norm.endpoints.values():
            ntype = _ENDPOINT_NODE.get(ep.kind, "endpoint")
            ep_id = f"{ep.method}:{ep.url}"
            self._node(ep_id, ntype, url=ep.url, method=ep.method, kind=ep.kind)
            host = _host_of(ep.url) or self.root
            if host:
                self._edge(host, ep_id, "contains_route")
            if ntype == "auth_surface":
                self._node(f"role:{host or 'site'}", "role")
                self._edge(ep_id, f"role:{host or 'site'}", "uses_auth")

        # parameters -> accepts_parameter
        for p in norm.params.values():
            pid = f"{p.url}?{p.name}"
            self._node(pid, "parameter", name=p.name, interest=p.interest)
            self._edge(p.url, pid, "accepts_parameter")

        # tls -> shares_certificate (root <-> SAN hosts)
        for info in norm.tls.values():
            for san in info.san or []:
                san = str(san).lstrip("*.").lower()
                if san and san != info.host:
                    self._node(san, "subdomain")
                    self._edge(info.host, san, "shares_certificate")

        # cloud candidates -> third_party + depends_on
        for cc in norm.cloud_candidates.values():
            tp = f"{cc.provider}:{cc.asset}"
            self._node(tp, "third_party", provider=cc.provider, asset=cc.asset)
            if self.root:
                self._edge(self.root, tp, "depends_on")

        # evidence: secret candidates (masked) + js intel
        for sc in norm.secret_candidates.values():
            eid = f"evidence:secret:{sc.secret_type}:{sc.masked}"
            self._node(eid, "evidence", evidence_type="secret_candidate", masked=sc.masked)
            self._edge(eid, sc.source, "linked_to_evidence")
        for js in norm.js_intel.values():
            if js.endpoints:
                eid = f"evidence:js:{js.url}"
                self._node(eid, "evidence", evidence_type="js_intel", count=len(js.endpoints))
                self._edge(eid, js.url, "linked_to_evidence")

        self._propagate_risk(priorities or [])
        return self.to_dict()

    # ----------------------------------------------------------------- #
    def _propagate_risk(self, priorities: "list[PriorityTarget]") -> None:
        """Seed risk from priority targets and propagate one hop along edges."""
        for t in priorities:
            for node_id, node in self._nodes.items():
                if node_id == t.asset or node_id.startswith(t.asset):
                    node["risk"] = max(node.get("risk", 0.0), float(t.score))
        # one-hop propagation (decayed) so connected assets inherit interest
        adj: dict[str, list[str]] = {}
        for e in self._edges:
            adj.setdefault(e["from"], []).append(e["to"])
            adj.setdefault(e["to"], []).append(e["from"])
        seeded = {nid: n["risk"] for nid, n in self._nodes.items() if n.get("risk", 0) > 0}
        for nid, risk in seeded.items():
            for nb in adj.get(nid, []):
                node = self._nodes.get(nb)
                if node is not None:
                    node["risk"] = max(node.get("risk", 0.0), risk * 0.4)

    def _clusters(self) -> list[dict[str, Any]]:
        """Cluster nodes by registrable host (simple, deterministic)."""
        clusters: dict[str, list[str]] = {}
        for nid, node in self._nodes.items():
            host = _host_of(nid) or (nid if node["type"] in ("domain", "subdomain", "ip") else "misc")
            parts = host.split(".")
            key = ".".join(parts[-2:]) if len(parts) >= 2 else (host or "misc")
            clusters.setdefault(key, []).append(nid)
        return [{"cluster": k, "size": len(v)} for k, v in sorted(clusters.items())]

    def to_dict(self) -> dict[str, Any]:
        nodes = list(self._nodes.values())
        type_counts: dict[str, int] = {}
        for n in nodes:
            type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1
        return {
            "root": self.root,
            "nodes": nodes,
            "edges": self._edges,
            "node_count": len(nodes),
            "edge_count": len(self._edges),
            "node_types": type_counts,
            "clusters": self._clusters(),
        }


def build_graph(norm: "ReconNormalizer", target: str = "",
                priorities: "list[PriorityTarget] | None" = None) -> dict[str, Any]:
    return AssetGraphBuilder(target).build(norm, priorities)
