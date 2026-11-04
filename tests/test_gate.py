"""Gate CI: 0 bersih, 2 ada temuan, severity tak dikenal tetap dihitung."""

import pytest

from wth_ai.redteam.gate import exit_code
from wth_ai.redteam.models import AttemptState, Finding


def _confirmed(severity: str) -> Finding:
    f = Finding(id="F-1", hypothesis="x", severity=severity)
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    return f


def test_no_findings_exits_zero():
    assert exit_code([]) == 0


def test_low_finding_below_threshold_passes():
    assert exit_code([_confirmed("low")], "high") == 0


def test_high_finding_fails_the_gate():
    assert exit_code([_confirmed("high")], "high") == 2


def test_unknown_severity_counts_as_finding():
    assert exit_code([_confirmed("???")]) == 2


def test_unknown_threshold_is_an_error():
    with pytest.raises(ValueError):
        exit_code([_confirmed("high")], "kritis")