"""scan-mode: default deep, quick paling ringan, key asing ditolak."""

import pytest

from wth_ai.redteam.scan_modes import SCAN_MODES, get_mode


def test_default_is_deep():
    assert get_mode(None).key == "deep"


def test_quick_is_lighter_than_deep():
    quick, deep = SCAN_MODES["quick"], SCAN_MODES["deep"]
    assert quick.max_attempts < deep.max_attempts
    assert quick.max_steps < deep.max_steps


def test_unknown_mode_lists_choices():
    with pytest.raises(ValueError) as excinfo:
        get_mode("turbo")
    assert "quick" in str(excinfo.value)