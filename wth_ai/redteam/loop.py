"""Loop red team: habiskan antrean, jangan berhenti di temuan pertama.

Perilaku `auto` ada di sini, bukan di prompt: tidak ada early exit, langkah yang
gagal dihitung gagal, dan kill switch diperiksa setiap iterasi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from wth_ai.redteam.classify import classify
from wth_ai.redteam.executor import AttemptExecutor
from wth_ai.redteam.models import AttemptState, Finding
from wth_ai.redteam.queue import FindingQueue


@dataclass
class LoopResult:
    attempted: int = 0
    confirmed: int = 0
    candidate: int = 0
    unproven: int = 0
    refuted: int = 0
    failures: int = 0
    killed: bool = False
    budget_hit: bool = False
    findings: list[Finding] = field(default_factory=list)


def _noop(name: str, data: dict) -> None:
    return None


@dataclass
class RedTeamLoop:
    executor: AttemptExecutor
    max_attempts: int = 3
    max_steps: int = 0  # 0 = tanpa batas
    is_killed: Callable[[], bool] = lambda: False
    on_event: Callable[[str, dict], None] = _noop

    def run(self, queue: FindingQueue) -> LoopResult:
        result = LoopResult()
        steps = 0
        seen: set[str] = set()
        while (finding := queue.next(exclude=seen)) is not None:
            if self.is_killed():
                result.killed = True
                break
            if self.max_steps and steps >= self.max_steps:
                result.budget_hit = True
                break
            seen.add(finding.id)
            steps += 1
            result.attempted += 1
            self._attempt(finding, result)
            self.on_event("redteam.finding", finding.to_dict())
        result.findings = queue.all()
        return result

    def _attempt(self, finding: Finding, result: LoopResult) -> None:
        for attempt_no in range(1, self.max_attempts + 1):
            finding.transition(AttemptState.ATTEMPTING)
            finding.attempts = attempt_no
            outcome = self.executor.attempt(finding, attempt_no=attempt_no)
            if outcome.failed_hard:
                # Langkah tidak bisa dijalankan BUKAN "tidak rentan": hitung
                # gagal, catat error, tapi tetap boleh dicoba lagi.
                result.failures += 1
                finding.evidence.extend(outcome.request_response)
                if outcome.error:
                    finding.evidence.append(f"error: {outcome.error}")
                finding.transition(AttemptState.UNPROVEN)
                continue
            state, attestation = classify(outcome)
            finding.attestation = attestation
            finding.evidence.extend(outcome.request_response)
            finding.transition(state)
            if state is not AttemptState.UNPROVEN:
                break
        if finding.state is AttemptState.CONFIRMED:
            result.confirmed += 1
        elif finding.state is AttemptState.CANDIDATE:
            result.candidate += 1
        elif finding.state is AttemptState.REFUTED:
            result.refuted += 1
        else:
            result.unproven += 1