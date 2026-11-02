"""Regression tests — WU-111: `ContextBuilder` unifikasi (ADR-004).

Bug asli: 2 jalur context-builder (`SupervisorAgent._build_context_block()`
vs `WTHOrchestrator._assess_loop()`) dipanggil dengan frekuensi SAMA (sekali
per giliran user, keduanya) tapi kualitas berbeda -- bukan trade-off performa
disengaja, melainkan drift organik dari dibangun di waktu berbeda.

Scope decision: HANYA `SupervisorAgent._build_context_block()` yang
dimigrasikan ke `ContextBuilder` di WU ini. `WTHOrchestrator._assess_loop()`
TIDAK dimigrasikan -- dia tidak membangun satu STRING context sama sekali,
melainkan mengoper 5+ potongan context terpisah sebagai keyword argument
langsung ke `PlannerAgent.plan()` (`resume_context`, `skill_context`,
`learning_context`, `graph_context`, `pre_hypotheses`, `chain_suggestions`)
-- calling convention yang struktural berbeda. Menyatukan itu butuh mengubah
signature `PlannerAgent.plan()` juga -- perubahan jauh lebih besar & berisiko
dari scope WU ini, ditunda bukan diabaikan diam-diam.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.context.builder import SOURCE_NAMES, ContextBuilder
from wth_ai.models import SessionState


def _state() -> SessionState:
    state = SessionState()
    state.scope.target = "example.com"
    state.scope.authorized = True
    return state


# ---------------------------------------------------------------------------
# Session summary -- selalu ada, tanpa syarat sources apa pun
# ---------------------------------------------------------------------------

def test_session_summary_always_included_even_with_no_sources():
    builder = ContextBuilder()
    out = builder.build(_state(), sources=[])
    assert "Target: example.com" in out
    assert "Authorized: True" in out


def test_session_summary_shows_unset_target():
    builder = ContextBuilder()
    out = builder.build(SessionState(), sources=[])
    assert "Target: unset" in out


# ---------------------------------------------------------------------------
# Setiap source -- resolve benar saat dependency ada, kosong saat None
# ---------------------------------------------------------------------------

def test_action_memory_source_included_when_provided():
    class _FakeActionMemory:
        def context_block(self):
            return "RECENT ACTIONS: did X"

    builder = ContextBuilder(action_memory=_FakeActionMemory())
    out = builder.build(_state(), sources=["action_memory"])
    assert "RECENT ACTIONS: did X" in out


def test_action_memory_source_empty_when_none():
    builder = ContextBuilder(action_memory=None)
    out = builder.build(_state(), sources=["action_memory"])
    assert "RECENT ACTIONS" not in out


def test_md_context_source_no_dependency_needed():
    """md_context selalu tersedia (module-level function, human-authored) --
    tidak butuh dependency injection sama sekali."""
    builder = ContextBuilder()
    out = builder.build(_state(), sources=["md_context"])
    assert isinstance(out, str)  # tidak crash walau MEMORY.md/USER.md kosong


def test_engagement_graph_source_empty_when_no_asset_graph():
    builder = ContextBuilder(asset_graph=None)
    out = builder.build(_state(), sources=["engagement_graph"])
    # tidak crash -- cuma tidak nambah apa pun selain session summary
    assert "Target: example.com" in out


def test_engagement_graph_source_uses_export_for_planner():
    class _FakeGraph:
        nodes = {"n1": object()}
        def export_for_planner(self):
            return "GRAPH: n1 -> endpoint"
        def coverage(self):
            return {"nodes": 1, "coverage_pct": 10}

    builder = ContextBuilder(asset_graph=_FakeGraph())
    out = builder.build(_state(), sources=["engagement_graph"])
    assert "GRAPH: n1 -> endpoint" in out
    assert "Graph nodes: 1 coverage: 10%" in out  # session summary JUGA pakai asset_graph


def test_learning_source_calls_build_planner_context_with_tech_profile():
    class _FakeLearning:
        def build_planner_context(self, tech_stack):
            return f"LEARNING for {tech_stack}"

    state = _state()
    state.metadata["tech_profile"] = "wordpress"
    builder = ContextBuilder(learning=_FakeLearning())
    out = builder.build(state, sources=["learning"])
    assert "LEARNING for wordpress" in out


def test_skill_fusion_source_empty_without_user_input():
    class _FakeSkillRouter:
        def fuse(self, user_input):
            raise AssertionError("tidak boleh terpanggil tanpa user_input")

    builder = ContextBuilder(skill_router=_FakeSkillRouter())
    state = _state()
    out = builder.build(state, sources=["skill_fusion"])  # user_input default ""
    assert out == builder.build(state, sources=[])  # tidak nambah apa pun -- state SAMA, bukan _state() baru


def test_skill_fusion_source_used_when_user_input_provided():
    class _FakePlaybook:
        def to_context(self):
            return "SKILLS: xss-testing"

    class _FakeSkillRouter:
        def fuse(self, user_input):
            assert user_input == "test the login form"
            return _FakePlaybook()

    builder = ContextBuilder(skill_router=_FakeSkillRouter())
    out = builder.build(_state(), sources=["skill_fusion"], user_input="test the login form")
    assert "SKILLS: xss-testing" in out


def test_fts_recall_source_always_empty_by_design():
    """ADR-004: SessionFtsIndex agent-pull-only lewat tool session_search --
    BUKAN missing feature, sengaja tidak auto-push ke context."""
    builder = ContextBuilder()
    state = _state()
    out = builder.build(state, sources=["fts_recall"])
    assert out == builder.build(state, sources=[])  # state SAMA, bukan _state() baru


def test_unknown_source_name_ignored_gracefully():
    builder = ContextBuilder()
    out = builder.build(_state(), sources=["totally_unknown_source_xyz"])
    assert "Target: example.com" in out  # tidak crash


# ---------------------------------------------------------------------------
# char_budget
# ---------------------------------------------------------------------------

def test_char_budget_truncates():
    class _FakeActionMemory:
        def context_block(self):
            return "X" * 1000

    builder = ContextBuilder(action_memory=_FakeActionMemory())
    out = builder.build(_state(), sources=["action_memory"], char_budget=50)
    assert len(out) == 50


def test_char_budget_zero_means_unbounded():
    builder = ContextBuilder()
    out = builder.build(_state(), sources=[], char_budget=0)
    assert "Target: example.com" in out


# ---------------------------------------------------------------------------
# SOURCE_NAMES -- kontrak lengkap sesuai ADR-004/INTERFACES.md
# ---------------------------------------------------------------------------

def test_all_six_source_names_handled_without_crashing():
    """Test kontrak sederhana (disebutkan ADR-004 §Trade-offs sebagai mitigasi
    drift): tiap entry SOURCE_NAMES harus resolve tanpa error, mengembalikan str."""
    builder = ContextBuilder()
    for name in SOURCE_NAMES:
        out = builder.build(_state(), sources=[name], user_input="test")
        assert isinstance(out, str)


def test_source_names_matches_adr004_interfaces_md():
    """Each source is one independent mechanism behind a thin shared contract.

    `jobs` was added deliberately: recon results live in the JOB store while a
    chat session's context comes from the SESSION asset graph, so the agent
    answered "belum ada recon di sesi ini" while completed jobs for that target
    sat on disk. Same pattern as the existing sources -- its own mechanism,
    selected per call site -- so the contract set gained an entry rather than
    the call sites growing bespoke assembly logic again.
    """
    assert set(SOURCE_NAMES) == {
        "action_memory", "md_context", "engagement_graph", "learning", "skill_fusion",
        "fts_recall", "jobs",
    }


# ---------------------------------------------------------------------------
# End-to-end -- SupervisorAgent._build_context_block() migrasi
# ---------------------------------------------------------------------------

def test_supervisor_build_context_block_uses_context_builder():
    from unittest.mock import MagicMock

    from wth_ai.agents.supervisor import SupervisorAgent
    from wth_ai.agents.supervisor_context import SupervisorContext
    from wth_ai.config import WTHConfig

    agent = SupervisorAgent(WTHConfig(), client=MagicMock())
    ctx = SupervisorContext(
        config=WTHConfig(), client=MagicMock(), state=_state(), scope_mgr=None, permission_mgr=None,
        asset_graph=None, evidence_vault=None, findings_db=None, terminal=None,
    )
    agent.bind(ctx)
    out = agent._build_context_block(_state())
    assert "Target: example.com" in out
    assert "Permission:" in out
    assert "Phase:" in out


def test_supervisor_build_context_block_includes_action_memory():
    from unittest.mock import MagicMock

    from wth_ai.agents.supervisor import SupervisorAgent
    from wth_ai.agents.supervisor_context import SupervisorContext
    from wth_ai.config import WTHConfig

    class _FakeActionMemory:
        def context_block(self):
            return "RECENT: probed /admin"

    agent = SupervisorAgent(WTHConfig(), client=MagicMock())
    ctx = SupervisorContext(
        config=WTHConfig(), client=MagicMock(), state=_state(), scope_mgr=None, permission_mgr=None,
        asset_graph=None, evidence_vault=None, findings_db=None, terminal=None,
        action_memory=_FakeActionMemory(),
    )
    agent.bind(ctx)
    out = agent._build_context_block(_state())
    assert "RECENT: probed /admin" in out
