"""Bridge Fase 5 normalized recon into AssetGraph."""

from __future__ import annotations

from urllib.parse import urlparse

from wth_ai.graph.asset_graph import AssetGraph, _param_priority
from wth_ai.graph.models import EdgeType, GraphNode, NodeType
from wth_ai.recon.normalizer import ReconNormalizer


def normalized_to_asset_graph(normalizer: ReconNormalizer, graph: AssetGraph, *, target: str = "") -> None:
    """Populate asset graph from a ReconNormalizer snapshot."""
    root = target
    host_node_id: str | None = None

    for host, _asset in normalizer.subdomains.items():
        root = root or host
        node = graph.add_node(GraphNode.create(NodeType.HOST, host, url=f"https://{host}"))
        if not host_node_id:
            host_node_id = node.id

    if root and not host_node_id:
        node = graph.add_node(GraphNode.create(NodeType.HOST, root, url=f"https://{root}"))
        host_node_id = node.id

    for probe in normalizer.http_probes.values():
        url = probe.url
        ep = graph.add_node(GraphNode.create(
            NodeType.ENDPOINT, url,
            method="GET",
            auth_required="/api/" in url or "/admin" in url,
        ))
        parsed = urlparse(url)
        if parsed.hostname:
            hk = graph._key(NodeType.HOST, parsed.hostname)
            if hk in graph._index:
                graph.add_edge(ep.id, graph._index[hk], EdgeType.HOSTED_ON)

    for url_asset in normalizer.urls.values():
        graph.add_node(GraphNode.create(NodeType.ENDPOINT, url_asset.url, method="GET"))

    for ep in normalizer.endpoints.values():
        graph.add_node(GraphNode.create(NodeType.ENDPOINT, ep.url, method=ep.method or "GET"))

    for param in normalizer.params.values():
        score = _param_priority(param.name)
        pnode = graph.add_node(GraphNode.create(
            NodeType.PARAMETER, f"{param.url}?{param.name}",
            url=param.url, param=param.name, score=score, priority=score,
        ))
        ep_key = graph._key(NodeType.ENDPOINT, param.url)
        if ep_key in graph._index:
            graph.add_edge(pnode.id, graph._index[ep_key], EdgeType.PARAM_OF)

    for js in normalizer.js_intel.values():
        graph.add_node(GraphNode.create(NodeType.JS_BUNDLE, js.url, url=js.url))

    for cloud in normalizer.cloud_candidates.values():
        label = cloud.asset or cloud.provider
        if label:
            graph.add_node(GraphNode.create(NodeType.ENDPOINT, label, tags=["cloud"], provider=cloud.provider))

    graph.save()
