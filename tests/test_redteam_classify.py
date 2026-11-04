"""Gate veracity: CONFIRMED hanya lewat assess_claim."""

from wth_ai.redteam.classify import classify
from wth_ai.redteam.executor import AttemptOutcome
from wth_ai.redteam.models import AttemptState


def test_full_evidence_confirms():
    outcome = AttemptOutcome(executed=True, request_response=["[TX:1]"],
                             reproductions=5, controls=1, tool_backed=True)
    state, attest = classify(outcome)
    assert state is AttemptState.CONFIRMED
    assert attest["level"] == "confirmed"


def test_missing_control_blocks_confirmation():
    outcome = AttemptOutcome(executed=True, request_response=["[TX:1]"],
                             reproductions=5, controls=0, tool_backed=True)
    state, _ = classify(outcome)
    assert state is not AttemptState.CONFIRMED


def test_control_refutes():
    outcome = AttemptOutcome(executed=True, request_response=["[TX:1]"],
                             reproductions=1, controls=1,
                             refuted_by_control=True)
    assert classify(outcome)[0] is AttemptState.REFUTED


def test_no_evidence_is_unproven():
    assert classify(AttemptOutcome())[0] is AttemptState.UNPROVEN