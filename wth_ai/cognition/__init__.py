"""WTH-AI cognition layer — explainable, learning-driven reasoning.

A deterministic, offline-capable reasoning brain that elevates the
recon -> planner -> executor pipeline:

  - signals          : grounded feature extraction
  - risk_model       : explainable weighted risk scoring + learning priors
  - reasoning_engine : dedup / kill / score / rank / plan-confidence
  - strategy_selector: UCB1 adaptive execution-strategy selection
  - engine           : CognitiveEngine facade used by the orchestrator

Safety: this layer never executes commands, escalates permissions, or bypasses
scope/authorization. It only prioritises what an authorized operator tests first.
"""

from __future__ import annotations

from wth_ai.cognition.engine import (
    AdaptiveStrategySelector,
    CognitiveEngine,
    ReasoningEngine,
    ReasoningReport,
    RiskModel,
    RiskScore,
    StrategyChoice,
)

__all__ = [
    "CognitiveEngine",
    "ReasoningEngine",
    "ReasoningReport",
    "RiskModel",
    "RiskScore",
    "AdaptiveStrategySelector",
    "StrategyChoice",
]
