"""Adaptive execution-strategy selection (UCB1 multi-armed bandit).

The executor historically always ran the same fixed chain per vuln class. This
selector instead treats each candidate tool chain as a bandit *arm* and balances
exploitation (chains that worked before, seeded from cross-session learning) with
exploration (under-tried chains), using the UCB1 acquisition rule. It adapts
within a session via ``record_outcome`` feedback and persists priors via the
learning store. Deterministic given identical state — safe to unit test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from wth_ai.cognition import signals
from wth_ai.models import PermissionMode


class _LearningLike(Protocol):
    def success_rate(self, tech_stack: str, vuln_class: str) -> float: ...


@dataclass(frozen=True)
class Strategy:
    name: str
    tools: tuple[str, ...]
    permission: PermissionMode
    note: str = ""


# Candidate execution strategies per vuln class. Permission stays at the
# minimum required; the PermissionManager still gates real execution, so this
# only *recommends* — it can never escalate privileges on its own.
STRATEGY_ARMS: dict[str, list[Strategy]] = {
    "idor": [
        Strategy("dual_account_diff", ("httpx", "diff_brain"), PermissionMode.STANDARD_TESTING,
                 "Baseline as user A, replay object id as user B, diff responses"),
        Strategy("sequential_id_walk", ("httpx", "curl"), PermissionMode.STANDARD_TESTING,
                 "Increment/realloc object ids and compare ownership signals"),
    ],
    "bola": [
        Strategy("dual_account_diff", ("httpx", "diff_brain"), PermissionMode.STANDARD_TESTING,
                 "Cross-tenant object access diff"),
    ],
    "ssrf": [
        Strategy("oob_callback", ("httpx", "interactsh-client"), PermissionMode.STANDARD_TESTING,
                 "Inject collaborator URL, watch for OOB callback"),
        Strategy("internal_probe", ("httpx", "curl"), PermissionMode.STANDARD_TESTING,
                 "Point fetch param at internal metadata/host placeholders"),
    ],
    "xss": [
        Strategy("reflection_marker", ("httpx", "curl"), PermissionMode.STANDARD_TESTING,
                 "Inject unique marker, check reflection + context"),
        Strategy("dom_sink_scan", ("katana", "dalfox"), PermissionMode.ELEVATED_APPROVAL,
                 "Crawl + DOM sink analysis (needs approval)"),
    ],
    "auth": [
        Strategy("token_replay", ("httpx", "jwt_tool"), PermissionMode.STANDARD_TESTING,
                 "Capture token, test reuse / role swap"),
    ],
    "auth_jwt": [
        Strategy("alg_confusion", ("jwt_tool", "httpx"), PermissionMode.STANDARD_TESTING,
                 "Test alg:none and key confusion on JWT"),
    ],
    "sqli": [
        Strategy("boolean_diff", ("httpx", "curl"), PermissionMode.STANDARD_TESTING,
                 "Boolean/time-based response differential"),
    ],
    "race": [
        Strategy("concurrent_burst", ("httpx", "curl"), PermissionMode.STANDARD_TESTING,
                 "Concurrent request burst on state-changing endpoint"),
    ],
    "unknown": [
        Strategy("safe_recon_probe", ("httpx", "curl"), PermissionMode.READ_ONLY_RECON,
                 "Read-only probe to gather more evidence before deciding"),
    ],
}


@dataclass
class StrategyChoice:
    vuln_class: str
    name: str
    tools: list[str]
    permission: str
    rationale: str
    ucb: float
    expected_success: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "vuln_class": self.vuln_class,
            "name": self.name,
            "tools": self.tools,
            "permission": self.permission,
            "rationale": self.rationale,
            "ucb": round(self.ucb, 3),
            "expected_success": round(self.expected_success, 3),
        }


@dataclass
class _ArmStats:
    pulls: int = 0
    reward_sum: float = 0.0

    @property
    def mean(self) -> float:
        return self.reward_sum / self.pulls if self.pulls else 0.0


class AdaptiveStrategySelector:
    """UCB1 selection over execution strategies, seeded by learning priors."""

    def __init__(self, *, exploration: float = 1.4) -> None:
        self.exploration = exploration
        # key: (vuln_class, strategy_name) -> in-session stats
        self._stats: dict[tuple[str, str], _ArmStats] = {}
        self._total_pulls: dict[str, int] = {}

    def _arm(self, vuln_class: str, name: str) -> _ArmStats:
        return self._stats.setdefault((vuln_class, name), _ArmStats())

    def select(
        self,
        vuln_class: str | None,
        *,
        tech_profile: str = "generic_web",
        learning: _LearningLike | None = None,
        exclude: set[str] | None = None,
    ) -> StrategyChoice | None:
        vc = signals.normalize_vuln_class(vuln_class)
        arms = STRATEGY_ARMS.get(vc) or STRATEGY_ARMS["unknown"]
        exclude = exclude or set()
        candidates = [a for a in arms if a.name not in exclude]
        if not candidates:
            return None

        prior = 0.0
        if learning is not None:
            try:
                prior = float(learning.success_rate(tech_profile, vc))
            except Exception:
                prior = 0.0

        total = max(1, self._total_pulls.get(vc, 0))
        best: tuple[float, Strategy, float] | None = None
        for arm in candidates:
            stats = self._arm(vc, arm.name)
            # Blend learning prior with observed in-session mean reward.
            if stats.pulls:
                exploit = 0.5 * prior + 0.5 * stats.mean
                bonus = self.exploration * math.sqrt(math.log(total + 1) / stats.pulls)
            else:
                # Unpulled arm: optimistic, prior-seeded, gets exploration boost.
                exploit = prior if prior else 0.5
                bonus = self.exploration  # strongly encourage trying it once
            ucb = exploit + bonus
            if best is None or ucb > best[0]:
                best = (ucb, arm, exploit)

        assert best is not None
        ucb, arm, exploit = best
        return StrategyChoice(
            vuln_class=vc,
            name=arm.name,
            tools=list(arm.tools),
            permission=arm.permission.value,
            rationale=arm.note,
            ucb=ucb,
            expected_success=exploit,
        )

    def record_outcome(self, vuln_class: str | None, strategy_name: str, *, success: bool) -> None:
        vc = signals.normalize_vuln_class(vuln_class)
        stats = self._arm(vc, strategy_name)
        stats.pulls += 1
        stats.reward_sum += 1.0 if success else 0.0
        self._total_pulls[vc] = self._total_pulls.get(vc, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            f"{vc}:{name}": {"pulls": s.pulls, "mean_reward": round(s.mean, 3)}
            for (vc, name), s in self._stats.items()
        }
