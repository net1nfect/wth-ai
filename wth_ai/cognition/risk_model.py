"""Explainable risk scoring for vulnerability hypotheses.

A transparent, weighted linear model over grounded signals (see ``signals.py``)
blended with cross-session learning priors. Deterministic and fully offline so
it can rank hypotheses before — and independently of — any LLM call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from wth_ai.cognition import signals


class _LearningLike(Protocol):
    def success_rate(self, tech_stack: str, vuln_class: str) -> float: ...


@dataclass
class RiskScore:
    """Explainable risk verdict for a single hypothesis."""

    score: float                       # 0..100
    confidence: float                  # 0..1
    severity_band: str                 # critical|high|medium|low|info
    factors: list[tuple[str, float]] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "severity_band": self.severity_band,
            "factors": [{"name": n, "contribution": round(c, 2)} for n, c in self.factors],
            "rationale": self.rationale,
        }


class RiskModel:
    """Weighted, explainable scorer. Higher score == test this first."""

    # Feature weights. Sum is normalised internally, so relative magnitude is
    # what matters. Tuned to favour impactful, well-grounded, surface-backed
    # hypotheses over generic guesses.
    WEIGHTS: dict[str, float] = {
        "base_severity": 0.30,
        "endpoint_exposure": 0.20,
        "param_sensitivity": 0.18,
        "profile_affinity": 0.10,
        "prior_priority": 0.12,
        "evidence_clarity": 0.10,
    }
    # Extra multiplicative lift from historical success (0..1) for this
    # (tech_profile, vuln_class) pair. Capped so memory informs but never
    # dominates first-principles severity.
    LEARNING_LIFT = 0.25

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or dict(self.WEIGHTS)
        self._wsum = sum(self.weights.values()) or 1.0

    def score(
        self,
        hypothesis: dict,
        *,
        tech_profile: str = "generic_web",
        learning: _LearningLike | None = None,
    ) -> RiskScore:
        feats = signals.extract_features(hypothesis, tech_profile=tech_profile)
        contributions: list[tuple[str, float]] = []
        base = 0.0
        for name, weight in self.weights.items():
            value = feats.get(name, 0.0)
            contribution = (weight / self._wsum) * value
            base += contribution
            contributions.append((name, contribution * 100))

        # Cross-session learning prior (Bayesian-ish lift).
        learning_rate = 0.0
        learning_n_known = False
        if learning is not None:
            try:
                learning_rate = float(
                    learning.success_rate(tech_profile, signals.normalize_vuln_class(hypothesis.get("vuln_class")))
                )
                learning_n_known = learning_rate > 0.0
            except Exception:
                learning_rate = 0.0
        lift = 1.0 + self.LEARNING_LIFT * learning_rate
        raw = min(1.0, base * lift)
        score = round(raw * 100, 1)
        if learning_rate:
            contributions.append(("learning_prior", round((lift - 1.0) * 100, 2)))

        confidence = self._confidence(feats, learning_known=learning_n_known)
        band = self._band(score)
        rationale = self._rationale(hypothesis, feats, learning_rate, band)
        contributions.sort(key=lambda c: -c[1])
        return RiskScore(
            score=score,
            confidence=confidence,
            severity_band=band,
            factors=contributions[:5],
            rationale=rationale,
        )

    @staticmethod
    def _confidence(feats: dict[str, float], *, learning_known: bool) -> float:
        # Confidence rises with grounding (clear evidence path, concrete
        # surface) and with available historical data.
        c = 0.35
        c += 0.25 * feats.get("evidence_clarity", 0.0)
        c += 0.20 * feats.get("endpoint_exposure", 0.0)
        c += 0.10 * feats.get("param_sensitivity", 0.0)
        if learning_known:
            c += 0.10
        return round(min(0.99, c), 2)

    @staticmethod
    def _band(score: float) -> str:
        if score >= 80:
            return "critical"
        if score >= 62:
            return "high"
        if score >= 42:
            return "medium"
        if score >= 25:
            return "low"
        return "info"

    @staticmethod
    def _rationale(hypothesis: dict, feats: dict[str, float], learning_rate: float, band: str) -> str:
        bits: list[str] = []
        vc = signals.normalize_vuln_class(hypothesis.get("vuln_class"))
        bits.append(f"{vc} class (base sev {feats['base_severity']:.2f})")
        if feats["endpoint_exposure"] >= 0.8:
            bits.append("high-exposure endpoint")
        if feats["param_sensitivity"] >= 0.7:
            bits.append("sensitive parameter")
        if feats["profile_affinity"] > 0:
            bits.append("tech-profile affinity")
        if learning_rate > 0:
            bits.append(f"memory success {int(learning_rate * 100)}%")
        return f"{band.upper()} — " + ", ".join(bits)
