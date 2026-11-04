"""FindingQueue: yang belum dicoba tetap terlihat."""

from wth_ai.redteam.models import AttemptState, Finding
from wth_ai.redteam.queue import FindingQueue


def test_next_returns_queued_then_skips_terminal():
    done = Finding(id="F-1", hypothesis="x", state=AttemptState.ATTEMPTING)
    done.transition(AttemptState.CONFIRMED)
    queued = Finding(id="F-2", hypothesis="y")
    queue = FindingQueue([done, queued])
    assert queue.next().id == "F-2"
    assert len(queue) == 1


def test_snapshot_contains_everything():
    queue = FindingQueue([Finding(id="F-1", hypothesis="a"),
                          Finding(id="F-2", hypothesis="b")])
    assert [f.id for f in queue.all()] == ["F-1", "F-2"]


def test_unproven_returns_to_queue():
    f = Finding(id="F-1", hypothesis="a")
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.UNPROVEN)
    queue = FindingQueue([f])
    assert queue.next() is f