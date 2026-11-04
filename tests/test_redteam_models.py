"""AttemptState: transisi ditegakkan kode."""

import pytest

from wth_ai.redteam.models import (AttemptState, Finding,
                                   InvalidAttemptTransition)


def _finding():
    return Finding(id="F-1", hypothesis="BOLA /orders")


def test_happy_path():
    f = _finding()
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    assert f.terminal is True


def test_confirmed_cannot_skip_attempt():
    f = _finding()
    with pytest.raises(InvalidAttemptTransition):
        f.transition(AttemptState.CONFIRMED)


def test_confirmed_is_final():
    f = _finding()
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    with pytest.raises(InvalidAttemptTransition):
        f.transition(AttemptState.ATTEMPTING)


def test_unproven_can_be_retried():
    f = _finding()
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.UNPROVEN)
    f.transition(AttemptState.ATTEMPTING)
    assert f.state is AttemptState.ATTEMPTING


def test_maps_to_engagement_lifecycle():
    f = _finding()
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    assert f.to_finding_state() == "verified"