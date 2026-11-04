"""Runner demo deterministik: membuktikan loop berjalan tanpa jaringan.

Dipakai `wth scan --offline-demo`. Sengaja memakai executor palsu: demo tidak
boleh bergantung pada jaringan, dan hasilnya harus sama setiap kali dijalankan
supaya bisa direkam.
"""

from __future__ import annotations

from wth_ai.redteam.executor import AttemptOutcome
from wth_ai.redteam.loop import LoopResult, RedTeamLoop
from wth_ai.redteam.models import Finding
from wth_ai.redteam.queue import FindingQueue


class _FixtureExecutor:
    def __init__(self) -> None:
        self._script = {
            "F-001": [AttemptOutcome(True, ["[TX:f001-a]"], 5, 1, tool_backed=True)],
            "F-002": [AttemptOutcome(True, ["[TX:f002-a]"], 2, 0)],
            "F-003": [],
        }

    def attempt(self, finding: Finding, *, attempt_no: int) -> AttemptOutcome:
        script = self._script.get(finding.id) or []
        if attempt_no <= len(script):
            return script[attempt_no - 1]
        return AttemptOutcome()


def demo_findings() -> list[Finding]:
    return [
        Finding(id="F-001", hypothesis="BOLA GET /api/v1/orders/{id}", vuln_class="bola",
                target="http://172.28.0.10", severity="high", provenance="NOVEL"),
        Finding(id="F-002", hypothesis="IDOR DELETE /api/v1/orders/{id}",
                vuln_class="idor", target="http://172.28.0.10", severity="medium"),
        Finding(id="F-003", hypothesis="GraphQL introspection terbuka",
                vuln_class="graphql", target="http://172.28.0.10"),
    ]


def run_offline_demo(*, max_attempts: int = 3, max_steps: int = 0) -> LoopResult:
    return RedTeamLoop(
        executor=_FixtureExecutor(),
        max_attempts=max_attempts,
        max_steps=max_steps,
    ).run(FindingQueue(demo_findings()))