"""CognitiveEngine — high-level facade tying the cognition layer together.

This is the single entry point the orchestrator uses. It composes:
  - RiskModel               (explainable hypothesis scoring)
  - ReasoningEngine         (dedup -> kill -> score -> rank -> confidence)
  - AdaptiveStrategySelector (UCB1 execution-strategy recommendation)

All guardrails remain upstream: nothing here executes commands, escalates
permissions, or bypasses scope/authorization. It only *reasons about and
prioritises* what an authorized operator/agent should test first.
"""

from __future__ import annotations

from typing import Any, Protocol

from wth_ai.cognition.reasoning_engine import ReasoningEngine, ReasoningReport
from wth_ai.cognition.risk_model import RiskModel, RiskScore
from wth_ai.cognition.strategy_selector import AdaptiveStrategySelector, StrategyChoice


class _LearningLike(Protocol):
    def success_rate(self, tech_stack: str, vuln_class: str) -> float: ...


class CognitiveEngine:
    """Composable reasoning brain for recon -> planner -> executor."""

    def __init__(
        self,
        *,
        risk_model: RiskModel | None = None,
        reasoning: ReasoningEngine | None = None,
        strategy: AdaptiveStrategySelector | None = None,
    ) -> None:
        self.risk_model = risk_model or RiskModel()
        self.reasoning = reasoning or ReasoningEngine(self.risk_model)
        self.strategy = strategy or AdaptiveStrategySelector()

    def deliberate(
        self,
        hypotheses: list[dict],
        *,
        tech_profile: str = "generic_web",
        learning: _LearningLike | None = None,
        killed_titles: set[str] | None = None,
        with_strategy: bool = True,
    ) -> ReasoningReport:
        return self.reasoning.deliberate(
            hypotheses,
            tech_profile=tech_profile,
            learning=learning,
            killed_titles=killed_titles,
            strategy_selector=self.strategy if with_strategy else None,
        )

    def score(
        self,
        hypothesis: dict,
        *,
        tech_profile: str = "generic_web",
        learning: _LearningLike | None = None,
    ) -> RiskScore:
        return self.risk_model.score(hypothesis, tech_profile=tech_profile, learning=learning)

    def recommend_strategy(
        self,
        vuln_class: str | None,
        *,
        tech_profile: str = "generic_web",
        learning: _LearningLike | None = None,
        exclude: set[str] | None = None,
    ) -> StrategyChoice | None:
        return self.strategy.select(
            vuln_class, tech_profile=tech_profile, learning=learning, exclude=exclude
        )

    def learn_from_outcome(self, vuln_class: str | None, strategy_name: str, *, success: bool) -> None:
        self.strategy.record_outcome(vuln_class, strategy_name, success=success)

    def prioritize_recon_targets(self, graph: Any, *, limit: int = 15) -> list[dict[str, Any]]:
        """Rank graph parameter/endpoint nodes for where recon/testing should focus."""
        out: list[dict[str, Any]] = []
        try:
            nodes = graph.query_high_value_params(limit * 2)
        except Exception:
            return out
        for node in nodes:
            data = getattr(node, "data", {}) or {}
            pseudo = {
                "title": f"focus {data.get('param', '')} @ {data.get('url', '')}",
                "component": data.get("url", ""),
                "param": data.get("param", ""),
                "vuln_class": "idor" if any(
                    k in str(data.get("param", "")).lower() for k in ("id", "user", "uuid")
                ) else "unknown",
                "priority": getattr(node, "priority", 5),
            }
            risk = self.risk_model.score(pseudo)
            out.append({
                "param": data.get("param", ""),
                "url": data.get("url", ""),
                "risk_score": risk.score,
                "severity_band": risk.severity_band,
            })
        out.sort(key=lambda d: -d["risk_score"])
        return out[:limit]


__all__ = [
    "CognitiveEngine",
    "ReasoningEngine",
    "ReasoningReport",
    "RiskModel",
    "RiskScore",
    "AdaptiveStrategySelector",
    "StrategyChoice",
]
