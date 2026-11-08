"""Loop: tanpa early exit, retry tiga kali, gagal keras dihitung."""

from wth_ai.redteam.executor import AttemptOutcome
from wth_ai.redteam.loop import RedTeamLoop
from wth_ai.redteam.models import AttemptState, Finding
from wth_ai.redteam.queue import FindingQueue


class _Exec:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def attempt(self, finding, *, attempt_no):
        self.calls.append((finding.id, attempt_no))
        return self.outcomes.get(finding.id, AttemptOutcome())


class _ExecWithContext(_Exec):
    """Executor yang meninggalkan konteks rantai pada finding."""

    def __init__(self, outcomes, context=None):
        super().__init__(outcomes)
        self.context = context or {}

    def attempt(self, finding, *, attempt_no):
        finding.attestation.update(self.context)
        return super().attempt(finding, attempt_no=attempt_no)


def _f(fid):
    return Finding(id=fid, hypothesis=f"hyp {fid}")


CONFIRMING = AttemptOutcome(True, ["[TX:1]"], 5, 1, tool_backed=True)


def test_all_findings_are_attempted_even_after_confirmation():
    exec_ = _Exec({"F-1": CONFIRMING, "F-2": CONFIRMING})
    queue = FindingQueue([_f("F-1"), _f("F-2"), _f("F-3")])
    result = RedTeamLoop(executor=exec_, max_attempts=1).run(queue)
    assert result.attempted == 3
    assert [c[0] for c in exec_.calls] == ["F-1", "F-2", "F-3"]
    assert result.confirmed == 2
    assert result.unproven == 1


def test_retries_three_times_then_unproven():
    exec_ = _Exec({})
    queue = FindingQueue([_f("F-9")])
    result = RedTeamLoop(executor=exec_, max_attempts=3).run(queue)
    assert [c[1] for c in exec_.calls] == [1, 2, 3]
    assert result.unproven == 1
    assert queue.all()[0].state is AttemptState.UNPROVEN


def test_kill_stops_immediately():
    exec_ = _Exec({})
    queue = FindingQueue([_f("F-1"), _f("F-2")])
    result = RedTeamLoop(executor=exec_, max_attempts=1,
                         is_killed=lambda: True).run(queue)
    assert result.killed is True
    assert result.attempted == 0


def test_failed_hard_counts_as_failure_not_skip():
    exec_ = _Exec({"F-1": AttemptOutcome(failed_hard=True, error="tool missing")})
    result = RedTeamLoop(executor=exec_, max_attempts=1).run(
        FindingQueue([_f("F-1")]))
    assert result.failures == 1
    assert result.unproven == 1


def test_max_steps_marks_budget_hit():
    exec_ = _Exec({})
    result = RedTeamLoop(executor=exec_, max_attempts=1, max_steps=1).run(
        FindingQueue([_f("F-1"), _f("F-2")]))
    assert result.budget_hit is True
    assert result.attempted == 1


def test_chain_context_survives_classify_and_is_mined():
    """Konteks rantai (credentials) menempel dan menghasilkan chain setelah loop."""
    exec_ = _ExecWithContext(
        {"F-1": CONFIRMING, "F-2": CONFIRMING, "F-3": CONFIRMING},
        context={"credentials": ["userB:pass"]},
    )
    queue = FindingQueue([_f("F-1"), _f("F-2"), _f("F-3")])
    result = RedTeamLoop(executor=exec_, max_attempts=1).run(queue)
    assert result.confirmed == 3
    # Setiap finding yang membawa credentials menghasilkan satu chain.
    assert len(result.chains) == 3
    assert all(c.root.startswith("F-") for c in result.chains)
    assert result.chains[0].links[0]["context"]["credentials"] == ["userB:pass"]