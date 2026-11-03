"""Regression tests — WU-011 (subset): default-flip `authorized=False`
(AGENT-01, trust boundary `scope_set`).

Bug asli: `authorized` BUKAN field required di schema tool `scope_set`
(`agents/tool_registry.py`) -- LLM bisa memanggil `scope_set(target=...)`
tanpa pernah menyebut otorisasi sama sekali, dan tiga tempat independen
diam-diam mendefaultkannya jadi True:
1. `supervisor_dispatch.py`: `args.get("authorized", True)` (jalur tool-call
   LLM langsung -- trust boundary paling kritis, actor yang sama yang baru
   menerima konten eksternal di context).
2. `scope_intent.py::_args_from_target()`: hardcode `"authorized": True` di
   SEMUA jalur, termasuk teks yang sama sekali tidak menyebut otorisasi
   ("set target x.com").
3. `cli/handlers/setup_handler.py::handle_target()`: fallback hardcode True.

Fix: ketiganya default False (fail-closed law: MISSING -> DENY). Teks/args
yang EKSPLISIT menyebut otorisasi ("authorized"/"izin", atau
`authorized=True` eksplisit di tool-call args) tetap berfungsi seperti
sebelumnya -- kapabilitas tidak hilang, cuma tidak lagi default permisif.

Scope decision (WORK-UNITS.md WU-011): HANYA default-flip yang
diimplementasikan di sini -- "bisa landing segera" sesuai catatan WU-011
sendiri. Rename tool `scope_set`/`permission_set` -> `propose_target`/
`request_profile_upgrade` DAN taint-tracking penuh (approval wajib kalau
context sudah tercemar konten eksternal) TIDAK disentuh -- butuh WU-007
(action-hashing + context.tainted) yang belum ada. Audit isi
`prompts/supervisor_system.txt` (apakah mereferensikan nama tool lama secara
tekstual) juga belum dilakukan -- dicatat sebagai follow-up, bukan
diabaikan.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.agents.supervisor_dispatch import dispatch_supervisor_tool
from wth_ai.agents.supervisor_context import SupervisorContext
from wth_ai.config import WTHConfig
from wth_ai.evidence.vault import EvidenceVault
from wth_ai.findings_db import FindingsDB
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.models import SessionState
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_intent import parse_scope_intent
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge


def _ctx(tmp_path) -> SupervisorContext:
    config = WTHConfig.load()
    return SupervisorContext(
        config=config,
        client=LLMProviderRouter(config),
        state=SessionState(),
        scope_mgr=ScopeManager(),
        permission_mgr=PermissionManager(),
        asset_graph=AssetGraph("test-scope-flip", base_dir=tmp_path),
        evidence_vault=EvidenceVault("test-scope-flip", base_dir=tmp_path),
        findings_db=FindingsDB(),
        terminal=AgentTerminalBridge(config, PermissionManager(), ScopeManager(), "test-scope-flip"),
    )


# ---------------------------------------------------------------------------
# scope_set tool-call (jalur LLM langsung) -- trust boundary paling kritis
# ---------------------------------------------------------------------------

def test_scope_set_tool_call_omitting_authorized_defaults_to_false(tmp_path):
    """REGRESI-GUARD utama: LLM memanggil scope_set(target=...) TANPA field
    authorized sama sekali (valid per schema -- authorized bukan required)
    -- sebelumnya diam-diam jadi authorized=True."""
    ctx = _ctx(tmp_path)
    dispatch_supervisor_tool(ctx, "scope_set", {"target": "evil-external.tld"}, approved=True)
    assert ctx.state.scope.authorized is False, (
        "REGRESI AGENT-01: scope_set tanpa field 'authorized' masih default "
        "True -- trust boundary LLM-proposed scope belum tertutup"
    )


def test_scope_set_tool_call_explicit_authorized_true_still_works(tmp_path):
    """Positive/compatibility: kapabilitas eksplisit authorized=True tidak hilang."""
    ctx = _ctx(tmp_path)
    dispatch_supervisor_tool(
        ctx, "scope_set", {"target": "example.com", "authorized": True}, approved=True,
    )
    assert ctx.state.scope.authorized is True


# ---------------------------------------------------------------------------
# scope_intent.py -- natural language / offline-fallback parser
# ---------------------------------------------------------------------------

def test_parse_scope_intent_no_authorization_language_defaults_false():
    args = parse_scope_intent("target evil.com")
    assert args is not None
    assert args["authorized"] is False


def test_parse_scope_intent_with_authorization_keyword_still_true():
    args = parse_scope_intent("scope evil.com authorized")
    assert args["authorized"] is True


def test_parse_scope_intent_indonesian_izin_keyword_still_true():
    args = parse_scope_intent("scope evil.com izin")
    assert args["authorized"] is True


def test_parse_scope_intent_bare_url_no_authorization_defaults_false():
    args = parse_scope_intent("https://evil.com/path")
    assert args is not None
    assert args["authorized"] is False


# ---------------------------------------------------------------------------
# End-to-end -- authorized=False dari default-flip benar-benar diberlakukan
# ReconEngine (WU-003) yang sudah lebih dulu benar
# ---------------------------------------------------------------------------

def test_default_flip_actually_blocks_recon_end_to_end(tmp_path, monkeypatch):
    """Bukti reachability: authorized=False hasil default-flip benar-benar
    sampai ke ReconEngine.run() dan memblokir -- bukan cuma field internal
    yang tidak dikonsumsi siapa pun."""
    from wth_ai.recon.engine import ReconEngine

    monkeypatch.delenv("WTH_REQUIRE_AUTHORIZED", raising=False)
    ctx = _ctx(tmp_path)
    dispatch_supervisor_tool(ctx, "scope_set", {"target": "evil-external.tld"}, approved=True)
    assert ctx.state.scope.authorized is False

    engine = ReconEngine(results_root=tmp_path)
    result = engine.run(ctx.state.scope.target, authorized=ctx.state.scope.authorized)
    assert result["status"] == "blocked"
