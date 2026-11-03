"""Regression tests — WU-119: `detect_intent()` session-aware fallback
(PRD-002 FR#4).

Bug asli: `SkillRouter.detect_intent()` SELALU fallback ke `"chat"` tanpa
peduli state sesi -- follow-up percakapan di tengah engagement aktif
(target sudah diset, phase bukan INTAKE) tapi tanpa keyword `assess_patterns`
eksplisit (mis. "ok lanjutkan", "coba yang itu") jatuh ke `ChatAgent`
(TANPA tool-calling) -- user kehilangan akses tool tanpa sadar di tengah
sesi hunt aktif.

Fix: parameter opsional `session_state=None` (backward-compatible, default
tidak mengubah perilaku existing). Kalau sesi punya target + phase != INTAKE,
intent jadi "assess" LEBIH DULU sebelum pattern-matching lama dijalankan --
"perbaikan satu kondisi, BUKAN classifier baru" (PRD-002 sendiri menolak
router multi-kategori di atas ReAct yang sudah adaptif).
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.models import AssessmentPhase, SessionState
from wth_ai.skills.fusion import SkillFusionEngine
from wth_ai.skills.registry import SkillRegistry
from wth_ai.skills.router import SkillRouter


def _router() -> SkillRouter:
    return SkillRouter(SkillRegistry())


def _active_session(phase: AssessmentPhase = AssessmentPhase.DYNAMIC) -> SessionState:
    state = SessionState()
    state.scope.target = "example.com"
    state.current_phase = phase
    return state


# ---------------------------------------------------------------------------
# Compatibility -- default (tanpa session_state) TIDAK berubah
# ---------------------------------------------------------------------------

def test_no_session_state_unchanged_behavior():
    router = _router()
    assert router.detect_intent("halo") == "chat"
    assert router.detect_intent("scan target ini") == "assess"
    assert router.detect_intent("/plan") == "command"


def test_session_state_none_explicit_unchanged_behavior():
    router = _router()
    assert router.detect_intent("halo", session_state=None) == "chat"


# ---------------------------------------------------------------------------
# REGRESI-GUARD utama -- session aktif override fallback "chat"
# ---------------------------------------------------------------------------

def test_ambiguous_followup_in_active_session_routes_to_assess():
    """Acceptance criterion literal PRD-002: follow-up tanpa keyword eksplisit
    tapi dalam sesi target+phase aktif -> 'assess', bukan 'chat'."""
    router = _router()
    state = _active_session()
    assert router.detect_intent("ok lanjutkan yang tadi", session_state=state) == "assess"


def test_session_without_target_falls_through_to_old_logic():
    router = _router()
    state = SessionState()  # target belum diset
    state.current_phase = AssessmentPhase.DYNAMIC
    assert router.detect_intent("halo", session_state=state) == "chat"


def test_session_at_intake_phase_falls_through_to_old_logic():
    """phase INTAKE = belum benar-benar 'di tengah' engagement -- fallback
    lama tetap berlaku meski target sudah diset."""
    router = _router()
    state = _active_session(phase=AssessmentPhase.INTAKE)
    assert router.detect_intent("halo", session_state=state) == "chat"


def test_command_prefix_still_takes_precedence_over_session_state():
    router = _router()
    state = _active_session()
    assert router.detect_intent("/help", session_state=state) == "command"


def test_explicit_assess_keyword_still_works_with_session_state():
    router = _router()
    state = _active_session()
    assert router.detect_intent("jalankan scan xss", session_state=state) == "assess"


# ---------------------------------------------------------------------------
# SkillFusionEngine wrapper -- thread session_state ke SkillRouter
# ---------------------------------------------------------------------------

def test_fusion_engine_threads_session_state_through():
    from wth_ai.config import WTHConfig

    engine = SkillFusionEngine(SkillRegistry(), WTHConfig())
    state = _active_session()
    assert engine.detect_intent("ok lanjutkan", session_state=state) == "assess"
    assert engine.detect_intent("ok lanjutkan") == "chat"  # default tanpa session_state


# ---------------------------------------------------------------------------
# End-to-end -- orchestrator.handle_input() benar-benar mengoper state
# ---------------------------------------------------------------------------

def test_orchestrator_passes_own_state_to_detect_intent():
    from unittest.mock import patch

    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig(), fresh=True)
    orch.state.scope.target = "example.com"
    orch.state.current_phase = AssessmentPhase.DYNAMIC

    seen_session_states = []
    original = orch.skill_router.detect_intent

    def _spy(user_input, *, session_state=None):
        seen_session_states.append(session_state)
        return original(user_input, session_state=session_state)

    with patch.object(orch.skill_router, "detect_intent", side_effect=_spy):
        orch.handle_input("ok lanjutkan yang tadi")

    assert seen_session_states, "detect_intent tidak pernah dipanggil"
    assert seen_session_states[0] is orch.state
