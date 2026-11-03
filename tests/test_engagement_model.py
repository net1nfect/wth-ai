"""Regression tests — WU-014: `Engagement` entity + FSM skeleton (ADR-011, PRD-003).

Skeleton murni (P0 slice PRD-003: "FSM + entity dasar") -- `EngagementStore`,
command `/engagement`, integrasi `ExecutionBroker` fetch-fresh, dan migrasi
`Scope` semuanya P1, TIDAK ada di WU ini. Kelas ini belum dipakai di manapun
lain di codebase (net-new file, blast radius nol terhadap kode existing) --
test di sini murni membuktikan FSM sesuai spesifikasi
`docs/architecture/STATE-MACHINES.md` §1 dan acceptance criteria literal
`docs/prd/PRD-003-engagement-management.md`.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from datetime import datetime, timedelta, timezone

import pytest

from wth_ai.engagement.model import (
    AuthorizationSource,
    Engagement,
    EngagementStatus,
    TargetScope,
    TimeWindow,
)


def _draft_engagement(**kwargs) -> Engagement:
    return Engagement(name="test-eng", created_by="cli-user", **kwargs)


def _ready_engagement() -> Engagement:
    eng = _draft_engagement(targets=[TargetScope(pattern="example.com")])
    eng.transition("submit_for_validation", actor="operator:cli")
    eng.authorization_source = AuthorizationSource(
        kind="bug_bounty_program", reference="h1-example", verified_by="operator:cli",
        verified_at=datetime.now(timezone.utc).isoformat(),
    )
    eng.transition("validation_passed", actor="operator:cli")
    return eng


# ---------------------------------------------------------------------------
# Happy path -- full lifecycle
# ---------------------------------------------------------------------------

def test_full_lifecycle_draft_to_completed():
    eng = _draft_engagement(targets=[TargetScope(pattern="example.com")])
    assert eng.status == EngagementStatus.DRAFT

    r = eng.transition("submit_for_validation", actor="operator:cli")
    assert r.success and eng.status == EngagementStatus.VALIDATING

    eng.authorization_source = AuthorizationSource(
        kind="bug_bounty_program", reference="h1-example", verified_by="operator:cli",
        verified_at=datetime.now(timezone.utc).isoformat(),
    )
    r = eng.transition("validation_passed", actor="operator:cli")
    assert r.success and eng.status == EngagementStatus.READY

    eng.approved_by = "operator:cli"
    r = eng.transition("activate", actor="operator:cli")
    assert r.success and eng.status == EngagementStatus.ACTIVE

    r = eng.transition("pause", actor="operator:cli")
    assert r.success and eng.status == EngagementStatus.PAUSED

    r = eng.transition("resume", actor="operator:cli")
    assert r.success and eng.status == EngagementStatus.ACTIVE

    r = eng.transition("finalize", actor="operator:cli")
    assert r.success and eng.status == EngagementStatus.COMPLETED


# ---------------------------------------------------------------------------
# Guard: DRAFT -> VALIDATING butuh target
# ---------------------------------------------------------------------------

def test_submit_for_validation_fails_without_targets():
    eng = _draft_engagement()
    r = eng.transition("submit_for_validation", actor="operator:cli")
    assert r.success is False
    assert eng.status == EngagementStatus.DRAFT


# ---------------------------------------------------------------------------
# Guard: VALIDATING -> READY butuh authorization_source + no conflict
# ---------------------------------------------------------------------------

def test_validation_passed_fails_without_authorization_source():
    eng = _draft_engagement(targets=[TargetScope(pattern="example.com")])
    eng.transition("submit_for_validation", actor="operator:cli")
    r = eng.transition("validation_passed", actor="operator:cli")
    assert r.success is False
    assert eng.status == EngagementStatus.VALIDATING


def test_validation_passed_fails_on_allowed_forbidden_conflict():
    eng = _draft_engagement(
        targets=[TargetScope(pattern="example.com")],
        allowed_operations=["shell.readonly"],
        forbidden_operations=["shell.readonly"],
    )
    eng.transition("submit_for_validation", actor="operator:cli")
    eng.authorization_source = AuthorizationSource(
        kind="lab_disposable", reference="local", verified_by="operator:cli",
        verified_at=datetime.now(timezone.utc).isoformat(),
    )
    r = eng.transition("validation_passed", actor="operator:cli")
    assert r.success is False
    assert "konflik" in r.reason.lower()


# ---------------------------------------------------------------------------
# Guard: READY -> ACTIVE -- literal acceptance criteria PRD-003
# ---------------------------------------------------------------------------

def test_activate_by_agent_actor_rejected():
    """PRD-003 acceptance criterion literal: Engagement.transition("activate",
    actor="supervisor") HARUS gagal."""
    eng = _ready_engagement()
    eng.approved_by = "operator:cli"
    for agent in ("supervisor", "planner", "executor", "recon"):
        r = eng.transition("activate", actor=agent)
        assert r.success is False, f"activate() lolos untuk actor agent '{agent}'"
        assert eng.status == EngagementStatus.READY


def test_activate_without_approved_by_rejected():
    """PRD-003 acceptance criterion literal: actor="operator:cli" dengan
    approved_by=None HARUS gagal."""
    eng = _ready_engagement()
    assert eng.approved_by is None
    r = eng.transition("activate", actor="operator:cli")
    assert r.success is False
    assert eng.status == EngagementStatus.READY


def test_activate_with_agent_string_as_approved_by_rejected():
    """Defense-in-depth: approved_by ITU SENDIRI tidak boleh string actor
    agent, bahkan kalau actor pemanggil transition() manusia."""
    eng = _ready_engagement()
    eng.approved_by = "supervisor"
    r = eng.transition("activate", actor="operator:cli")
    assert r.success is False
    assert eng.status == EngagementStatus.READY


def test_activate_by_human_with_approved_by_succeeds():
    eng = _ready_engagement()
    eng.approved_by = "operator:cli"
    r = eng.transition("activate", actor="operator:cli")
    assert r.success is True
    assert eng.status == EngagementStatus.ACTIVE


# ---------------------------------------------------------------------------
# abort/killswitch -- selalu boleh dari state non-terminal manapun
# ---------------------------------------------------------------------------

def test_abort_allowed_from_any_non_terminal_state():
    for setup in (
        lambda: _draft_engagement(),
        lambda: _ready_engagement(),
    ):
        eng = setup()
        r = eng.transition("abort", actor="operator:cli")
        assert r.success is True
        assert eng.status == EngagementStatus.ABORTED


def test_transition_after_terminal_state_rejected():
    eng = _draft_engagement()
    eng.transition("abort", actor="operator:cli")
    assert eng.status == EngagementStatus.ABORTED
    r = eng.transition("submit_for_validation", actor="operator:cli")
    assert r.success is False
    assert "terminal" in r.reason.lower()


# ---------------------------------------------------------------------------
# EXPIRED -- otomatis dari time_window, bukan mutasi manual
# ---------------------------------------------------------------------------

def test_active_engagement_auto_expires_when_time_window_passed():
    eng = _ready_engagement()
    eng.approved_by = "operator:cli"
    eng.transition("activate", actor="operator:cli")
    assert eng.status == EngagementStatus.ACTIVE

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    eng.time_window = TimeWindow(expires_at=past)
    assert eng.status == EngagementStatus.EXPIRED


def test_paused_engagement_also_auto_expires():
    eng = _ready_engagement()
    eng.approved_by = "operator:cli"
    eng.transition("activate", actor="operator:cli")
    eng.transition("pause", actor="operator:cli")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    eng.time_window = TimeWindow(expires_at=past)
    assert eng.status == EngagementStatus.EXPIRED


def test_not_yet_expired_stays_active():
    eng = _ready_engagement()
    eng.approved_by = "operator:cli"
    eng.transition("activate", actor="operator:cli")
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    eng.time_window = TimeWindow(expires_at=future)
    assert eng.status == EngagementStatus.ACTIVE


def test_expired_engagement_rejects_further_transitions():
    eng = _ready_engagement()
    eng.approved_by = "operator:cli"
    eng.transition("activate", actor="operator:cli")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    eng.time_window = TimeWindow(expires_at=past)
    r = eng.transition("pause", actor="operator:cli")
    assert r.success is False


# ---------------------------------------------------------------------------
# Enforcement-by-construction -- status read-only
# ---------------------------------------------------------------------------

def test_status_attribute_assignment_raises_attributeerror():
    eng = _draft_engagement()
    with pytest.raises(AttributeError):
        eng.status = EngagementStatus.ACTIVE  # type: ignore[misc]


def test_unknown_event_rejected():
    eng = _draft_engagement(targets=[TargetScope(pattern="example.com")])
    r = eng.transition("nonexistent_event", actor="operator:cli")
    assert r.success is False


# ---------------------------------------------------------------------------
# Serialization sanity
# ---------------------------------------------------------------------------

def test_to_dict_roundtrip_basic_fields():
    eng = _draft_engagement(targets=[TargetScope(pattern="example.com")])
    d = eng.to_dict()
    assert d["status"] == "draft"
    assert d["created_by"] == "cli-user"
    assert d["targets"][0]["pattern"] == "example.com"
    assert d["id"].startswith("ENG-")
