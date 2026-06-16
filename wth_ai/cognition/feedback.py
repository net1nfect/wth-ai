"""Feedback controller — closes the learning loop (flowchart 10 / L6).

Routes outcomes from the executor and verifier back into the cognition layer:
  - execution success/failure -> AdaptiveStrategySelector (UCB1) + learning store
  - verifier verdict           -> cross-session learning + a kill-set that
    suppresses disproven hypotheses on the next deliberation.

Deterministic and offline; no network or execution happens here.
"""

from __future__ import annotations

from typing import Any, Protocol

from wth_ai.cognition import signals


class _StrategyLike(Protocol):
    def record_outcome(self, vuln_class: str | None, strategy_name: str, *, success: bool) -> None: ...


class _LearningLike(Protocol):
    def record_from_finding(self, tech_stack: str, vuln_class: str, *, verified: bool,
                            tools: list[str] | None = ..., endpoint: str = ..., session_id: str = ...) -> None: ...


class FeedbackController:
    """Persist outcomes so the next cycle is smarter than the last."""

    def __init__(self, strategy: _StrategyLike, learning: _LearningLike | None = None) -> None:
        self.strategy = strategy
        self.learning = learning
        self._killed: set[str] = set()
        self._executions = 0
        self._verdicts = 0

    def record_execution(
        self,
        *,
        vuln_class: str | None,
        strategy_name: str,
        success: bool,
        tech_profile: str = "generic_web",
        endpoint: str = "",
        tools: list[str] | None = None,
        session_id: str = "",
    ) -> None:
        vc = signals.normalize_vuln_class(vuln_class)
        self.strategy.record_outcome(vc, strategy_name, success=success)
        if self.learning is not None:
            try:
                self.learning.record_from_finding(
                    tech_profile, vc, verified=success,
                    tools=tools or [], endpoint=endpoint, session_id=session_id,
                )
            except Exception:
                pass
        self._executions += 1

    def record_verdict(
        self,
        title: str,
        vuln_class: str | None,
        *,
        verified: bool,
        tech_profile: str = "generic_web",
        endpoint: str = "",
        session_id: str = "",
    ) -> None:
        vc = signals.normalize_vuln_class(vuln_class)
        if not verified and title:
            self._killed.add(title)
        if self.learning is not None:
            try:
                self.learning.record_from_finding(
                    tech_profile, vc, verified=verified,
                    endpoint=endpoint, session_id=session_id,
                )
            except Exception:
                pass
        self._verdicts += 1

    def kill(self, title: str) -> None:
        if title:
            self._killed.add(title)

    def killed_titles(self) -> set[str]:
        return set(self._killed)

    def snapshot(self) -> dict[str, Any]:
        return {
            "executions_recorded": self._executions,
            "verdicts_recorded": self._verdicts,
            "killed": sorted(self._killed),
            "strategy_arms": getattr(self.strategy, "snapshot", dict)(),
        }
