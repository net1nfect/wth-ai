"""Fase 6 P3 tests: asset graph (13 node / 12 edge types) + risk engine."""

from __future__ import annotations

from wth_ai.recon import scoring
from wth_ai.recon.asset_graph import EDGE_TYPES, NODE_TYPES, build_graph
from wth_ai.recon.model import ToolRun
from wth_ai.recon.normalizer import ReconNormalizer


def _rich_normalizer() -> ReconNormalizer:
    norm = ReconNormalizer()
    norm.add_run(ToolRun(tool="builtin", normalized_items=[
        {"kind": "subdomain", "host": "admin.example.com"},
        {"kind": "subdomain", "host": "api.example.com"},
        {"kind": "dns_record", "host": "admin.example.com", "record_type": "A", "value": "1.2.3.4"},
        {"kind": "url", "url": "https://admin.example.com/login"},
        {"kind": "http_probe", "url": "https://admin.example.com/login", "status": 403,
         "server": "cloudflare"},
        {"kind": "endpoint", "url": "https://api.example.com/graphql", "endpoint_kind": "graphql"},
        {"kind": "endpoint", "url": "https://admin.example.com/login", "endpoint_kind": "auth"},
        {"kind": "endpoint", "url": "https://api.example.com/upload", "endpoint_kind": "upload"},
        {"kind": "endpoint", "url": "wss://api.example.com/ws", "endpoint_kind": "websocket"},
        {"kind": "parameter", "url": "https://api.example.com/x", "name": "redirect", "interest": 7},
        {"kind": "tls", "host": "example.com", "san": ["example.com", "vpn.example.com"]},
        {"kind": "cloud_candidate", "provider": "aws_s3", "asset": "bucket.s3.amazonaws.com"},
        {"kind": "secret_candidate", "source": "https://api.example.com/app.js",
         "secret_type": "aws_access_key", "masked": "AKIA***LE"},
        {"kind": "js_intel", "url": "https://api.example.com/app.js",
         "endpoints": ["/api/v1/orders"]},
    ]))
    return norm


def test_graph_has_many_node_types():
    norm = _rich_normalizer()
    targets = scoring.score(norm)
    g = build_graph(norm, target="example.com", priorities=targets)
    present_node_types = set(g["node_types"])
    # expect a broad spread of node types
    assert {"domain", "subdomain", "ip", "url", "api_route", "auth_surface",
            "upload_surface", "websocket", "parameter", "third_party",
            "evidence", "role"} <= present_node_types
    assert all(t in NODE_TYPES for t in present_node_types)


def test_graph_has_many_edge_types():
    norm = _rich_normalizer()
    g = build_graph(norm, target="example.com")
    edge_types = {e["type"] for e in g["edges"]}
    assert {"resolves_to", "hosts", "exposes", "contains_route", "uses_auth",
            "accepts_parameter", "shares_certificate", "depends_on",
            "served_by", "linked_to_evidence"} <= edge_types
    assert all(t in EDGE_TYPES for t in edge_types)


def test_graph_clusters_and_counts():
    norm = _rich_normalizer()
    g = build_graph(norm, target="example.com")
    assert g["node_count"] == len(g["nodes"])
    assert g["edge_count"] == len(g["edges"])
    assert g["clusters"]


def test_risk_propagation_marks_connected_nodes():
    norm = _rich_normalizer()
    targets = scoring.score(norm)
    g = build_graph(norm, target="example.com", priorities=targets)
    risky = [n for n in g["nodes"] if n.get("risk", 0) > 0]
    assert risky  # priority targets seeded risk into the graph


def test_scoring_assigns_severity_and_confidence():
    norm = _rich_normalizer()
    targets = scoring.score(norm)
    assert targets
    top = targets[0]
    assert top.severity in ("critical", "high", "medium", "low", "info")
    assert 0.0 <= top.confidence <= 1.0
    # secret candidate + admin should push something to high/critical
    assert any(t.severity in ("high", "critical") for t in targets)


def test_risk_heatmap_buckets():
    norm = _rich_normalizer()
    targets = scoring.score(norm)
    heat = scoring.risk_heatmap(targets)
    assert set(heat) == {"critical", "high", "medium", "low", "info"}
    total = sum(len(v) for v in heat.values())
    assert total == len(targets)


def test_scoring_backward_compatible_admin_top():
    """Preserve the legacy contract: admin asset scores highest."""
    norm = ReconNormalizer()
    norm.add_run(ToolRun(tool="builtin", normalized_items=[
        {"kind": "subdomain", "host": "admin.example.com"},
        {"kind": "http_probe", "url": "https://admin.example.com/", "status": 403},
        {"kind": "subdomain", "host": "static.example.com"},
    ]))
    targets = scoring.score(norm)
    assert targets[0].score >= 1
    assert "admin" in targets[0].asset
