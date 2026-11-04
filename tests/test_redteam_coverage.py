"""Coverage: penyebut selalu ikut, pembagi nol tidak melempar."""

from wth_ai.redteam.coverage import Coverage


def test_zero_denominator_is_safe():
    cov = Coverage(0, 0, 0)
    assert cov.attempted_ratio == 0.0
    assert cov.hit_ratio == 0.0


def test_ratios_and_line():
    cov = Coverage(surfaces_total=10, surfaces_attempted=4, findings_confirmed=2)
    assert cov.attempted_ratio == 0.4
    assert cov.hit_ratio == 0.5
    assert "4/10" in cov.line()