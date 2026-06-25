"""Fase 6 P1 tests: execution-mode router + hardened job admission policy."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.jobs.policy import PolicyEngine
from wth_ai.policy import EXECUTION_MODES, capability, resolve_mode


# --------------------------------------------------------------------------- #
# Execution-mode router
# --------------------------------------------------------------------------- #
def test_all_six_modes_present():
    assert set(EXECUTION_MODES) == {
        "passive-only", "safe-active", "authenticated-safe",
        "approval-required", "report-only", "recovery",
    }


def test_passive_only_forbids_active_and_network():
    cap = capability("passive-only")
    assert not cap.allow_network
    assert not cap.allow_active
    assert not cap.requires_approval


def test_approval_required_mode_gates_mutation():
    cap = capability("approval-required")
    assert cap.allow_mutation
    assert cap.requires_approval
    assert cap.permits_risk("high")


def test_safe_active_is_safe_only():
    cap = capability("safe-active")
    assert cap.allow_active
    assert not cap.allow_mutation
    assert cap.permits_risk("safe")
    assert not cap.permits_risk("high")


def test_resolve_mode_falls_back_to_profile_default():
    assert resolve_mode(None, profile="budget") == "passive-only"
    assert resolve_mode(None, profile="recon-only") == "safe-active"
    assert resolve_mode("authenticated-safe", profile="budget") == "authenticated-safe"
    assert resolve_mode("nonsense", profile="default") == "safe-active"


# --------------------------------------------------------------------------- #
# Policy admission
# --------------------------------------------------------------------------- #
def test_admit_rejects_unknown_type():
    d = PolicyEngine().admit(type="bogus", target="x.com")
    assert not d.allowed and "unknown job type" in d.reason


def test_admit_requires_target_for_recon():
    d = PolicyEngine().admit(type="recon", target=None)
    assert not d.allowed and "requires a target" in d.reason


def test_admit_safe_mode_enforced():
    d = PolicyEngine().admit(type="recon", target="x.com", safe_mode=False)
    assert not d.allowed


def test_target_classification():
    pe = PolicyEngine()
    assert pe.classify_target("example.com") == "domain"
    assert pe.classify_target("https://example.com/a") == "url"
    assert pe.classify_target("8.8.8.8") == "ip"
    assert pe.classify_target("10.0.0.0/24") == "cidr"
    assert pe.classify_target("127.0.0.1") == "local"


def test_environment_tagging():
    pe = PolicyEngine()
    assert pe.tag_environment("staging.example.com") == "staging"
    assert pe.tag_environment("dev.example.com") == "dev"
    assert pe.tag_environment("localhost") == "lab"
    assert pe.tag_environment("shop.example.com") == "prod"


def test_sensitive_action_classifier():
    pe = PolicyEngine()
    assert pe.classify_sensitivity("https://example.com/") == "safe"
    assert pe.classify_sensitivity("https://admin.example.com/") == "medium"
    assert pe.classify_sensitivity("https://admin.example.com/api/v1/payment") == "high"
    # executor jobs are always at least medium
    assert pe.classify_sensitivity("https://example.com/", job_type="executor") == "medium"


def test_allowlist_blocks_out_of_scope():
    pe = PolicyEngine()
    ok, _ = pe.check_allow_deny("evil.com", allowlist=["*.example.com"])
    assert not ok
    ok, _ = pe.check_allow_deny("api.example.com", allowlist=["*.example.com"])
    assert ok


def test_denylist_and_forbidden_block():
    pe = PolicyEngine()
    ok, reason = pe.check_allow_deny("prod.internal", forbidden=("internal",))
    assert not ok and "forbidden" in reason
    ok, reason = pe.check_allow_deny("10.0.0.5", denylist=["10.0.0.0/8"])
    assert not ok and "denylist" in reason


def test_admit_prod_high_sensitivity_requires_approval():
    d = PolicyEngine().admit(type="recon", target="https://admin.example.com/api/v1/payment")
    assert d.allowed
    assert d.environment == "prod"
    assert d.sensitivity == "high"
    assert d.requires_approval
    assert d.rate_rps <= 1.0  # prod rate cap


def test_admit_executor_blocked_in_passive_mode():
    d = PolicyEngine().admit(type="executor", target="example.com", execution_mode="passive-only")
    assert not d.allowed and "forbids active" in d.reason


def test_evaluate_backward_compatible():
    pe = PolicyEngine()
    ok, reason = pe.evaluate(type="recon", target="prod.internal", forbidden=("internal",))
    assert not ok and "forbidden" in reason
    ok, _ = pe.evaluate(type="recon", target="example.com")
    assert ok
