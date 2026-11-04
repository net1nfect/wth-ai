"""Mode agen: auto adalah superset, pecahan lebih rendah otonominya."""

import pytest

from wth_ai.modes import (BY_KEY, MODES, Cap, CapabilityDenied, capability_header,
                          check, matrix_rows, next_mode, require)


def test_seven_modes():
    assert len(MODES) == 7
    assert set(BY_KEY) == {"plan", "build", "auto", "scout", "strike",
                           "chain", "scribe"}


def test_auto_is_the_superset():
    auto = BY_KEY["auto"]
    assert auto.caps == frozenset(Cap)
    for mode in MODES:
        assert mode.caps <= auto.caps, f"{mode.key} punya cap di luar auto"


def test_plan_cannot_exploit():
    assert check(BY_KEY["plan"], Cap.EXPLOIT) is False
    with pytest.raises(CapabilityDenied):
        require(BY_KEY["plan"], Cap.EXPLOIT)


def test_fragments_are_lower_autonomy_than_auto():
    auto = BY_KEY["auto"]
    for key in ("scout", "strike", "chain"):
        assert BY_KEY[key].autonomy < auto.autonomy


def test_matrix_has_one_row_per_mode():
    rows = matrix_rows()
    assert len(rows) == len(MODES)
    assert len(rows[0]) == len(capability_header())


def test_next_mode_wraps():
    assert next_mode("scribe", 1) == "plan"
    assert next_mode("plan", -1) == "scribe"