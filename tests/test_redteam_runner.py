"""Demo offline: deterministik, tiga temuan, tiga status berbeda."""

from wth_ai.redteam.runner import run_offline_demo


def test_offline_demo_is_deterministic():
    first = run_offline_demo()
    second = run_offline_demo()
    assert first.attempted == second.attempted == 3
    assert first.confirmed == 1
    assert first.candidate == 1
    assert first.unproven == 1
    assert [f.state.value for f in first.findings] == \
           [f.state.value for f in second.findings]