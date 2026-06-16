"""Reasoning engine — turns raw hypotheses into a grounded, ranked action plan.

Pipeline: normalise -> deduplicate -> drop killed/disproven -> risk-score
-> attach adaptive execution strategy -> rank -> compute plan confidence.

Deterministic and offline. The LLM planner can consume the resulting ordering
and rationales as high-signal context, but the prioritisation itself never
depends on a model being available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from wth_ai.cognition import signals
from wth_ai.cognition.risk_model import RiskModel, RiskScore
from wth_ai.cognition.strategy_selector import AdaptiveStrategySelector, StrategyChoice


class _LearningLike(Protocol):
    def success_rate(self, tech_stack: str, vuln_class: str) -> float: ...


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


@dataclass
class ReasonedHypothesis:
    hypothesis: dict
    risk: RiskScore
    strategy: StrategyChoice | None = None
    rank: int = 0

    def to_dict(self) -> dict[str, Any]:
        h = dict(self.hypothesis)
        h["risk"] = self.risk.to_dict()
        h["risk_score"] = self.risk.score
        h["severity_band"] = self.risk.severity_band
        h["priority"] = max(1, min(10, round(self.risk.score / 10)))
        if self.strategy is not None:
            h["recommended_strategy"] = self.strategy.to_dict()
        h["rank"] = self.rank
        return h


@dataclass
class ReasoningReport:
    items: list[ReasonedHypothesis] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    deduped: int = 0
    tech_profile: str = "generic_web"
    plan_confidence: float = 0.0

    def ranked_hypotheses(self) -> list[dict[str, Any]]:
        return [it.to_dict() for it in self.items]

    def top(self, n: int = 5) -> list[ReasonedHypothesis]:
        return self.items[:n]

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "tech_profile": self.tech_profile,
            "plan_confidence": self.plan_confidence,
            "deduped": self.deduped,
            "killed": self.killed,
            "ranked": [
                {
                    "rank": it.rank,
                    "title": it.hypothesis.get("title", ""),
                    "vuln_class": signals.normalize_vuln_class(it.hypothesis.get("vuln_class")),
                    "risk_score": it.risk.score,
                    "severity_band": it.risk.severity_band,
                    "confidence": it.risk.confidence,
                    "strategy": it.strategy.name if it.strategy else None,
                }
                for it in self.items
            ],
        }

    def format_cli(self) -> str:
        if not self.items:
            return (
                "## Cognition — Reasoned Plan\n"
                "No hypotheses yet. Run `/recon` (or `/scope set ...` then `/recon`) "
                "to populate the asset graph first."
            )
        lines = [
            "## Cognition — Reasoned Plan",
            f"- Tech profile: `{self.tech_profile}`",
            f"- Plan confidence: {int(self.plan_confidence * 100)}%",
            f"- Hypotheses: {len(self.items)} ranked"
            + (f" · {self.deduped} merged" if self.deduped else "")
            + (f" · {len(self.killed)} killed" if self.killed else ""),
            "",
            "### Prioritised hypotheses",
        ]
        for it in self.items[:10]:
            h = it.hypothesis
            vc = signals.normalize_vuln_class(h.get("vuln_class"))
            lines.append(
                f"{it.rank}. [{it.risk.severity_band.upper()} {it.risk.score:.0f}] "
                f"{h.get('title', vc)}"
            )
            lines.append(f"   - why: {it.risk.rationale} (conf {int(it.risk.confidence * 100)}%)")
            if it.strategy is not None:
                lines.append(
                    f"   - strategy: `{it.strategy.name}` via {', '.join(it.strategy.tools)} "
                    f"[{it.strategy.permission}] — {it.strategy.rationale}"
                )
        if self.killed:
            lines.append("")
            lines.append("### Killed (low value / out of scope shape)")
            for k in self.killed[:8]:
                lines.append(f"- {k}")
        return "\n".join(lines)


class ReasoningEngine:
    """Deliberation pipeline over hypotheses."""

    def __init__(
        self,
        risk_model: RiskModel | None = None,
        *,
        min_score: float = 18.0,
    ) -> None:
        self.risk_model = risk_model or RiskModel()
        self.min_score = min_score

    def deliberate(
        self,
        hypotheses: list[dict],
        *,
        tech_profile: str = "generic_web",
        learning: _LearningLike | None = None,
        killed_titles: set[str] | None = None,
        strategy_selector: AdaptiveStrategySelector | None = None,
    ) -> ReasoningReport:
        killed_norm = {_norm_title(t) for t in (killed_titles or set())}
        seen: dict[str, dict] = {}
        deduped = 0
        killed_out: list[str] = []

        for hyp in hypotheses or []:
            title = hyp.get("title", "")
            ntitle = _norm_title(title)
            if not ntitle:
                continue
            if ntitle in killed_norm:
                killed_out.append(title)
                continue
            vc = signals.normalize_vuln_class(hyp.get("vuln_class"))
            comp = (hyp.get("component") or "").lower()
            key = f"{vc}|{comp}|{ntitle}"
            if key in seen:
                deduped += 1
                # keep the higher-priority duplicate
                if hyp.get("priority", 0) > seen[key].get("priority", 0):
                    seen[key] = hyp
                continue
            seen[key] = hyp

        scored: list[ReasonedHypothesis] = []
        for hyp in seen.values():
            risk = self.risk_model.score(hyp, tech_profile=tech_profile, learning=learning)
            if risk.score < self.min_score:
                killed_out.append(hyp.get("title", "") + f" (score {risk.score:.0f} < {self.min_score:.0f})")
                continue
            strategy = None
            if strategy_selector is not None:
                strategy = strategy_selector.select(
                    hyp.get("vuln_class"), tech_profile=tech_profile, learning=learning
                )
            scored.append(ReasonedHypothesis(hypothesis=hyp, risk=risk, strategy=strategy))

        scored.sort(
            key=lambda it: (
                -it.risk.score,
                -signals.vuln_base_severity(it.hypothesis.get("vuln_class")),
                -it.risk.confidence,
            )
        )
        for idx, it in enumerate(scored, start=1):
            it.rank = idx

        plan_confidence = self._plan_confidence(scored)
        return ReasoningReport(
            items=scored,
            killed=killed_out,
            deduped=deduped,
            tech_profile=tech_profile,
            plan_confidence=plan_confidence,
        )

    @staticmethod
    def _plan_confidence(items: list[ReasonedHypothesis]) -> float:
        if not items:
            return 0.0
        top = items[: min(5, len(items))]
        # Confidence-weighted mean of top normalised risk scores.
        num = sum((it.risk.score / 100) * it.risk.confidence for it in top)
        den = sum(it.risk.confidence for it in top) or 1.0
        return round(num / den, 2)
