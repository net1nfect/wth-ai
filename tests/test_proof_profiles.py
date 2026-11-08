"""Test proof profiles deterministik + negative-control (fitur Strix)."""

from __future__ import annotations

import pytest

from wth_ai.redteam.proof_profiles import (
    ProofVerdict,
    UnknownCategoryError,
    evaluate_proof,
)


def test_known_category_returns_verdict():
    v = evaluate_proof(
        "ssrf", {"negative_control": "ok", "callback_received": False}
    )
    assert isinstance(v, ProofVerdict)


def test_unknown_category_raises():
    with pytest.raises(UnknownCategoryError):
        evaluate_proof("nope", {"negative_control": "ok"})


def test_ssrf_positive_requires_callback():
    # SSRF harus punya bukti callback (requests.catcher / collaborator).
    v = evaluate_proof(
        "ssrf",
        {
            "negative_control": "no_callback_non_ssrf",
            "callback_received": True,
            "internal_url_evidence": "http://169.254.169.254",
        },
    )
    assert v is ProofVerdict.INDEPENDENTLY_VERIFIED


def test_ssrf_rejected_without_callback():
    v = evaluate_proof(
        "ssrf",
        {
            "negative_control": "port_open_from_outside",
            "callback_received": False,
        },
    )
    assert v is ProofVerdict.REJECTED


def test_ssrf_inconclusive_without_negative_control():
    with pytest.raises(ValueError, match="negative_control"):
        evaluate_proof("ssrf", {"callback_received": True})


def test_idor_requires_two_identities():
    v = evaluate_proof(
        "idor",
        {
            "negative_control": "foreign_no_access",
            "identity_a": "user-a",
            "identity_b": "user-b",
            "owned_baseline": "a_can_read_own",
            "foreign_attempt": "a_reads_b_record",
            "unauthorized_state_delta": "b_record_leaked",
        },
    )
    assert v is ProofVerdict.INDEPENDENTLY_VERIFIED


def test_idor_rejected_foreign_denied():
    v = evaluate_proof(
        "idor",
        {
            "negative_control": "foreign_no_access",
            "identity_a": "user-a",
            "identity_b": "user-b",
            "owned_baseline": "a_can_read_own",
            "foreign_attempt": "a_reads_b_record",
            "unauthorized_state_delta": "denied",
        },
    )
    assert v is ProofVerdict.REJECTED


def test_xss_positive_requires_execution_evidence():
    v = evaluate_proof(
        "xss",
        {
            "negative_control": "payload_escaped_by_default",
            "execution_evidence": "alert_1_fired",
        },
    )
    assert v is ProofVerdict.INDEPENDENTLY_VERIFIED


def test_xss_rejected_payload_escaped():
    v = evaluate_proof(
        "xss",
        {
            "negative_control": "payload_escaped_by_default",
            "execution_evidence": "none",
        },
    )
    assert v is ProofVerdict.REJECTED


def test_sql_injection_positive_requires_db_effect():
    v = evaluate_proof(
        "sql_injection",
        {
            "negative_control": "parameterized_no_effect",
            "db_effect_evidence": "error_based_dup_key",
        },
    )
    assert v is ProofVerdict.INDEPENDENTLY_VERIFIED


def test_sql_injection_rejected_no_effect():
    v = evaluate_proof(
        "sql_injection",
        {
            "negative_control": "parameterized_no_effect",
            "db_effect_evidence": "none",
        },
    )
    assert v is ProofVerdict.REJECTED


def test_authorization_positive_requires_role_delta():
    v = evaluate_proof(
        "authorization",
        {
            "negative_control": "regular_user_denied",
            "privileged_role_ok": True,
            "lower_role_gain": True,
        },
    )
    assert v is ProofVerdict.INDEPENDENTLY_VERIFIED


def test_authorization_rejected_no_role_gain():
    v = evaluate_proof(
        "authorization",
        {
            "negative_control": "regular_user_denied",
            "privileged_role_ok": True,
            "lower_role_gain": False,
        },
    )
    assert v is ProofVerdict.REJECTED
