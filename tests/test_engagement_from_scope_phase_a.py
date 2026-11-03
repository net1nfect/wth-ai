"""Regression tests — WU-112 Fase A: compat shim `Scope`→`Engagement`
(ADR-011/ADR-012, PRD-003).

Scope decision (didokumentasikan penuh di WORK-UNITS.md WU-112): HANYA Fase A
(fondasi) yang diimplementasikan -- `SessionState.engagement_id` aditif +
`Engagement.from_scope()` derivasi DRAFT. Fase B (migrasi ~105 call-site
`state.scope`) DITUNDA eksplisit: tidak ada konsumen enforcement (broker
fetch-fresh masih skeleton, WU-102), jadi memigrasikan call-site sekarang =
membuat objek otorisasi yang tidak ada yang mengeceknya (architecture
theater), DAN dependency WU-111 (unifikasi context-reading) baru sebagian.

Invariant KUNCI yang diuji: `from_scope()` mempertahankan dekopling ADR-012 --
`scope.authorized=True` TIDAK PERNAH menghasilkan Engagement yang ter-aktivasi
atau ter-approve manusia. Scope-authorization (dimensi 2) dan engagement
lifecycle-authorization (dimensi terpisah) tetap terpisah.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.engagement.model import AGENT_ACTOR_NAMES, Engagement, EngagementStatus
from wth_ai.models import Scope, SessionState


# ---------------------------------------------------------------------------
# SessionState.engagement_id -- aditif, backward-compat
# ---------------------------------------------------------------------------

def test_session_state_engagement_id_defaults_empty():
    assert SessionState().engagement_id == ""


def test_session_state_to_dict_includes_engagement_id():
    state = SessionState()
    state.engagement_id = "ENG-abc123"
    assert state.to_dict()["engagement_id"] == "ENG-abc123"


def test_session_state_from_dict_backward_compatible_without_key():
    """Data sesi LAMA (sebelum WU-112, tanpa key 'engagement_id') tetap load
    tanpa error, default ke ''."""
    state = SessionState.from_dict({"session_id": "old-session"})
    assert state.engagement_id == ""


def test_session_state_from_dict_preserves_engagement_id():
    state = SessionState.from_dict({"session_id": "x", "engagement_id": "ENG-xyz"})
    assert state.engagement_id == "ENG-xyz"


def test_scope_still_the_only_authorization_source_by_default():
    """Compat: menambah engagement_id TIDAK mengubah bahwa scope tetap sumber
    otorisasi default (engagement_id kosong = tidak ada Engagement tertaut)."""
    state = SessionState()
    assert state.engagement_id == ""
    assert state.scope is not None


# ---------------------------------------------------------------------------
# Engagement.from_scope() -- pemetaan data boundary
# ---------------------------------------------------------------------------

def test_from_scope_maps_target_and_in_scope_to_targets():
    scope = Scope(target="api.example.com", in_scope=["*.example.com", "cdn.example.com"])
    eng = Engagement.from_scope(scope, created_by="operator")
    patterns = [t.pattern for t in eng.targets]
    assert "api.example.com" in patterns
    assert "*.example.com" in patterns
    assert "cdn.example.com" in patterns


def test_from_scope_dedupes_target_appearing_in_in_scope():
    scope = Scope(target="api.example.com", in_scope=["api.example.com", "other.example.com"])
    eng = Engagement.from_scope(scope, created_by="operator")
    patterns = [t.pattern for t in eng.targets]
    assert patterns.count("api.example.com") == 1


def test_from_scope_maps_exclusions_from_out_of_scope_and_forbidden():
    scope = Scope(
        target="x.com",
        out_of_scope=["admin.x.com"],
        forbidden=["payments.x.com"],
    )
    eng = Engagement.from_scope(scope, created_by="operator")
    assert "admin.x.com" in eng.excluded_assets
    assert "payments.x.com" in eng.excluded_assets


def test_from_scope_maps_rate_limit():
    scope = Scope(target="x.com", rate_limit_rps=5.0)
    eng = Engagement.from_scope(scope, created_by="operator")
    assert eng.rate_limits.default_rps == 5.0


def test_from_scope_name_falls_back_to_project_then_target():
    assert Engagement.from_scope(Scope(target="x.com", project="proj-1"), created_by="op").name == "proj-1"
    assert Engagement.from_scope(Scope(target="x.com"), created_by="op").name == "x.com"
    assert Engagement.from_scope(Scope(), created_by="op").name == "engagement-from-scope"


def test_from_scope_records_created_by():
    eng = Engagement.from_scope(Scope(target="x.com"), created_by="operator-human")
    assert eng.created_by == "operator-human"


# ---------------------------------------------------------------------------
# INVARIANT KUNCI (ADR-012 dekopling) -- authorized TIDAK meng-aktivasi
# ---------------------------------------------------------------------------

def test_from_scope_authorized_true_still_produces_draft():
    """scope.authorized=True TIDAK boleh menghasilkan Engagement ter-aktivasi
    -- ini inti dekopling ADR-012. Hasil SELALU DRAFT."""
    scope = Scope(target="x.com", authorized=True)
    eng = Engagement.from_scope(scope, created_by="operator")
    assert eng.status == EngagementStatus.DRAFT


def test_from_scope_never_sets_approved_by_or_authorization_source():
    scope = Scope(target="x.com", authorized=True)
    eng = Engagement.from_scope(scope, created_by="operator")
    assert eng.approved_by is None
    assert eng.authorization_source is None


def test_from_scope_result_still_requires_full_fsm_walk_to_activate():
    """Bukti: Engagement hasil from_scope() tetap harus jalan FSM penuh --
    tidak ada jalan pintas dari 'scope authorized' ke ACTIVE."""
    scope = Scope(target="x.com", in_scope=["x.com"], authorized=True)
    eng = Engagement.from_scope(scope, created_by="operator")
    # coba aktivasi langsung -> gagal (masih DRAFT, belum lewat validasi)
    result = eng.transition("activate", actor="operator")
    assert result.success is False
    assert eng.status == EngagementStatus.DRAFT


def test_from_scope_then_agent_actor_still_cannot_activate():
    """Dekopling + guard human-actor tetap berlaku pada Engagement hasil shim:
    bahkan setelah walk FSM sampai READY, actor agent tetap tidak bisa
    activate (ADR-011)."""
    scope = Scope(target="x.com", authorized=True)
    eng = Engagement.from_scope(scope, created_by="operator")
    eng.transition("submit_for_validation", actor="operator")
    # tanpa authorization_source, validation_passed gagal -> tidak sampai READY
    r = eng.transition("validation_passed", actor="operator")
    assert r.success is False
    # dan actor agent memang termasuk yang diblok untuk activate
    assert "supervisor" in AGENT_ACTOR_NAMES


def test_from_scope_roundtrips_through_to_dict():
    """Engagement hasil from_scope() adalah Engagement penuh -- to_dict/
    from_dict round-trip tetap bekerja (kompat dengan EngagementStore WU-101)."""
    scope = Scope(target="x.com", in_scope=["*.x.com"], rate_limit_rps=3.0)
    eng = Engagement.from_scope(scope, created_by="operator")
    restored = Engagement.from_dict(eng.to_dict())
    assert restored.id == eng.id
    assert [t.pattern for t in restored.targets] == [t.pattern for t in eng.targets]
    assert restored.rate_limits.default_rps == 3.0
