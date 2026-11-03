"""Regression tests — WU-113: merge `EngagementMemory` → `AssetGraph` (ADR-004).

Bug 1 (dead fields, dihapus): `controllers`/`middleware`/`trust_boundaries`
diinisialisasi, di-(de)serialize, tapi TIDAK PERNAH diisi metode manapun
(`update_from_recon`/`sync_from_graph`/`update_from_executor`) -- diverifikasi
via grep menyeluruh sebelum dihapus, tidak direferensikan di luar file ini
sama sekali juga.

Bug 2 (ditemukan & ditutup sekaligus): `sync_from_graph()`'s `self.routes.
extend(routes[:50])` TANPA dedup -- setiap pemanggilan berulang menumpuk
entry duplikat. SUDAH live sebelum WU ini (mis. `/hunt` dipanggil >1x dalam
satu sesi, `orch.memory` persisten lintas panggilan `EngagementLoop`) --
jadi prasyarat WAJIB sebelum context_block() bisa auto-sync tiap panggilan
tanpa membocorkan duplikasi tak terbatas.

Fix utama (ADR-004 "merge ke AssetGraph"): `asset_graph` jadi referensi
opsional pasca-konstruksi (constructor ordering di orchestrator.py membangun
EngagementMemory SEBELUM AssetGraph) -- `context_block()` auto-sync dari situ
begitu wired, caller tidak perlu ingat memanggil `sync_from_graph()` manual
supaya datanya fresh.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.graph.models import GraphNode, NodeType
from wth_ai.memory.engagement_memory import EngagementMemory


def _memory(tmp_path) -> EngagementMemory:
    return EngagementMemory("test-session", base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Dead fields dihapus
# ---------------------------------------------------------------------------

def test_dead_fields_no_longer_exist(tmp_path):
    mem = _memory(tmp_path)
    assert not hasattr(mem, "controllers")
    assert not hasattr(mem, "middleware")
    assert not hasattr(mem, "trust_boundaries")


def test_to_dict_no_longer_includes_dead_fields(tmp_path):
    mem = _memory(tmp_path)
    d = mem.to_dict()
    assert "controllers" not in d
    assert "middleware" not in d
    assert "trust_boundaries" not in d


def test_load_old_memory_json_with_dead_fields_does_not_crash(tmp_path):
    """Compatibility: memory.json LAMA (tersimpan sebelum WU ini) yang masih
    punya controllers/middleware/trust_boundaries tetap bisa di-load --
    field itu diam-diam diabaikan, bukan error."""
    import json

    path = tmp_path / "old-session" / "memory.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "session_id": "old-session", "assets": ["a.com"], "routes": [],
        "controllers": ["UserController"], "middleware": ["auth"],
        "trust_boundaries": ["internal"], "input_surfaces": [], "rejected_hypotheses": [],
        "evidence_gaps": [], "iteration": 3, "tech_profile": "laravel",
    }))
    mem = EngagementMemory("old-session", base_dir=tmp_path)
    assert mem.assets == ["a.com"]
    assert mem.iteration == 3
    assert not hasattr(mem, "controllers")


# ---------------------------------------------------------------------------
# sync_from_graph() dedup fix
# ---------------------------------------------------------------------------

def _graph_with_nodes(tmp_path) -> AssetGraph:
    graph = AssetGraph("test-session", base_dir=tmp_path)
    graph.add_node(GraphNode.create(NodeType.HOST, "api.example.com"))
    graph.add_node(GraphNode.create(NodeType.ENDPOINT, "https://api.example.com/v1"))
    return graph


def test_sync_from_graph_no_duplicate_routes_on_repeated_call(tmp_path):
    """REGRESI-GUARD utama: memanggil sync_from_graph() 3x berturut-turut
    dengan graph YANG SAMA tidak boleh melipatgandakan routes."""
    mem = _memory(tmp_path)
    graph = _graph_with_nodes(tmp_path)
    mem.sync_from_graph(graph)
    count_after_first = len(mem.routes)
    mem.sync_from_graph(graph)
    mem.sync_from_graph(graph)
    assert len(mem.routes) == count_after_first
    assert count_after_first > 0


def test_sync_from_graph_adds_new_routes_from_growing_graph(tmp_path):
    """Compatibility: node BARU yang ditambahkan graph tetap ter-sync,
    dedup tidak sampai memblokir data baru yang genuinely baru."""
    mem = _memory(tmp_path)
    graph = _graph_with_nodes(tmp_path)
    mem.sync_from_graph(graph)
    count_before = len(mem.routes)
    graph.add_node(GraphNode.create(NodeType.ENDPOINT, "https://api.example.com/v2"))
    mem.sync_from_graph(graph)
    assert len(mem.routes) == count_before + 1


def test_assets_still_deduped_as_before():
    """assets sudah dedup lewat dict.fromkeys sejak awal -- pastikan tidak
    ikut berubah oleh fix routes."""
    mem = EngagementMemory.__new__(EngagementMemory)
    mem.assets = ["a.com"]
    mem.routes = []
    mem.session_id = "x"

    class _FakeNode:
        def __init__(self, label, ntype):
            self.label = label
            self.type = ntype

    class _FakeGraph:
        nodes = {"1": _FakeNode("a.com", NodeType.HOST)}

    import wth_ai.memory.engagement_memory as em_mod
    original_save = EngagementMemory.save
    EngagementMemory.save = lambda self: None
    try:
        mem.sync_from_graph(_FakeGraph())
    finally:
        EngagementMemory.save = original_save
    assert mem.assets == ["a.com"]  # tidak duplikat


# ---------------------------------------------------------------------------
# context_block() auto-sync
# ---------------------------------------------------------------------------

def test_context_block_auto_syncs_when_asset_graph_wired(tmp_path):
    mem = _memory(tmp_path)
    graph = _graph_with_nodes(tmp_path)
    mem.asset_graph = graph
    out = mem.context_block()
    assert "ROUTES: 1 mapped" in out or "ROUTES:" in out
    assert len(mem.routes) > 0  # benar-benar ke-sync, bukan cuma dibaca


def test_context_block_does_not_crash_without_asset_graph(tmp_path):
    """Compatibility: asset_graph=None (default) -- context_block() tetap
    berfungsi seperti sebelum WU ini, tidak crash."""
    mem = _memory(tmp_path)
    assert mem.asset_graph is None
    out = mem.context_block()
    assert "ITERATION:" in out


def test_context_block_repeated_calls_stay_stable(tmp_path):
    """Bukti kunci: context_block() dipanggil berkali-kali (realistis --
    dipanggil tiap giliran) TIDAK membuat routes membengkak tanpa batas."""
    mem = _memory(tmp_path)
    graph = _graph_with_nodes(tmp_path)
    mem.asset_graph = graph
    mem.context_block()
    mem.context_block()
    mem.context_block()
    # graph fixture: 1 HOST + 1 ENDPOINT = 2 routes (sync_from_graph menghitung
    # keduanya) -- poin ujinya: TETAP 2 meski context_block() dipanggil 3x
    # (dedup bekerja), bukan 6 (3x lipat gandakan tanpa dedup).
    assert len(mem.routes) == 2


# ---------------------------------------------------------------------------
# orchestrator wiring -- asset_graph benar-benar sampai ke memory
# ---------------------------------------------------------------------------

def test_orchestrator_wires_asset_graph_into_memory():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    assert orch.memory.asset_graph is orch.asset_graph


def test_new_session_rewires_asset_graph_into_memory():
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    orch.new_session()
    assert orch.memory.asset_graph is orch.asset_graph
