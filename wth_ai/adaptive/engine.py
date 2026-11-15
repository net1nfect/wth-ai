"""Adaptive campaign engine: observe → classify → mutate → measure → learn.

The engine only acts through a caller-supplied `probe` callable (production:
`TerminalBridge.dispatch_tool("http_probe", ...)`), so every request still goes
through the same audit/transcript path. Enforcement is not re-implemented here;
when no-limits is on, the underlying gate is already open.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .memory import TargetMemory
from .request import HttpRequest
from .signals import Signal, classify
from .strategies import Move, propose

Probe = Callable[[dict[str, Any]], Any]


@dataclass
class Observation:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    analysis: str = ""
    transcript_id: str = ""
    url: str = ""
    method: str = ""
    error: str = ""

    @classmethod
    def from_result(cls, result: Any) -> "Observation":
        if result is None:
            return cls(error="no result")
        headers = getattr(result, "response_headers", None) or getattr(result, "headers", None) or {}
        if not isinstance(headers, Mapping):
            headers = {}
        return cls(
            status=int(getattr(result, "status_code", 0) or 0),
            headers={str(k): str(v) for k, v in headers.items()},
            body=str(getattr(result, "stdout", "") or ""),
            analysis=str(getattr(result, "analysis", "") or ""),
            transcript_id=str(getattr(result, "transcript_id", "") or ""),
            url=str(getattr(result, "url", "") or ""),
            method=str(getattr(result, "method", "") or ""),
            error=str(getattr(result, "stderr", "") or ""),
        )

    def signal(self, baseline_status: int = 0) -> Signal:
        return classify(self.status, self.headers, self.body, analysis=self.analysis,
                        baseline_status=baseline_status)


@dataclass
class AdaptiveAttempt:
    round: int
    strategy: str
    category: str
    description: str
    method: str
    url: str
    status: int
    signal: str
    vendor: str
    improved: bool
    transcript_id: str = ""
    rationale: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round, "strategy": self.strategy, "category": self.category,
            "description": self.description, "method": self.method, "url": self.url,
            "status": self.status, "signal": self.signal, "vendor": self.vendor,
            "improved": self.improved, "transcript_id": self.transcript_id,
            "rationale": self.rationale, "note": self.note,
        }


@dataclass
class AdaptiveReport:
    target: str
    seed: str
    baseline_status: int = 0
    baseline_signal: str = ""
    vendor: str = ""
    rounds: int = 0
    attempts: list[AdaptiveAttempt] = field(default_factory=list)
    wins: int = 0
    best_strategy: str = ""
    stopped_reason: str = ""
    memory_path: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def bypassed(self) -> bool:
        return any(a.improved for a in self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target, "seed": self.seed,
            "baseline_status": self.baseline_status, "baseline_signal": self.baseline_signal,
            "vendor": self.vendor, "rounds": self.rounds, "wins": self.wins,
            "bypassed": self.bypassed, "best_strategy": self.best_strategy,
            "stopped_reason": self.stopped_reason, "memory_path": self.memory_path,
            "attempts": [a.to_dict() for a in self.attempts],
            "started_at": self.started_at,
            "elapsed_s": round(time.time() - self.started_at, 2),
        }


def _better(baseline: Observation, current: Observation) -> bool:
    """A bypass is only claimed when a blocked baseline turns non-blocked."""
    b = baseline.signal()
    c = current.signal()
    if not b.is_blocked:
        return False
    if c.is_blocked or c.status == 0:
        return False
    if c.status >= 500:
        return False
    return c.kind in {"success", "redirect"}


class AdaptiveEngine:
    def __init__(self, target: str, *, memory: TargetMemory | None = None, memory_dir=None) -> None:
        self.target = target
        self.memory = memory or TargetMemory.load(target, memory_dir)

    def _execute(self, probe: Probe, move: Move, *, round_no: int) -> Observation:
        try:
            result = probe(move.request.to_probe(purpose=f"adaptive:{move.strategy}"))
        except Exception as exc:  # noqa: BLE001
            return Observation(error=f"{type(exc).__name__}: {exc}")
        return Observation.from_result(result)

    def _record(self, attempt: AdaptiveAttempt) -> None:
        self.memory.record(
            attempt.strategy, signal=attempt.signal, status=attempt.status,
            improved=attempt.improved, evidence=attempt.transcript_id,
        )

    def _attempt(self, move: Move, obs: Observation, baseline: Observation, round_no: int) -> AdaptiveAttempt:
        signal = obs.signal(baseline_status=baseline.status)
        return AdaptiveAttempt(
            round=round_no, strategy=move.strategy, category=move.category,
            description=move.description, method=move.request.method, url=move.request.url,
            status=obs.status, signal=signal.kind, vendor=signal.vendor,
            improved=_better(baseline, obs), transcript_id=obs.transcript_id,
            rationale=move.rationale, note=obs.error,
        )

    def retry_blocked(
        self,
        seed: HttpRequest,
        first_result: Any,
        probe: Probe,
        *,
        limit: int = 4,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> list[AdaptiveAttempt]:
        """One-shot bypass retry for an already-blocked request (autopilot hook)."""
        baseline = Observation.from_result(first_result)
        bsignal = baseline.signal()
        if not bsignal.is_blocked:
            return []
        moves = propose(seed, signal_kind=bsignal.kind, vendor=bsignal.vendor,
                        memory=self.memory, limit=limit)
        out: list[AdaptiveAttempt] = []
        for move in moves:
            if progress:
                progress("adaptive_strategy", {
                    "target": self.target, "strategy": move.strategy,
                    "url": move.request.url, "score": move.score,
                })
            obs = self._execute(probe, move, round_no=1)
            attempt = self._attempt(move, obs, baseline, 1)
            self._record(attempt)
            out.append(attempt)
            if progress:
                progress("adaptive_result", attempt.to_dict())
            if attempt.improved:
                break
        return out

    def run(
        self,
        seed: HttpRequest,
        probe: Probe,
        *,
        max_rounds: int = 3,
        moves_per_round: int = 6,
        stop_on_success: bool = True,
        explore: bool = False,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AdaptiveReport:
        report = AdaptiveReport(
            target=self.target, seed=seed.url, memory_path=str(self.memory.path),
        )
        baseline = self._execute(probe, Move("baseline", "baseline", "seed request", seed, 1.0, "seed"), round_no=0)
        bsignal = baseline.signal()
        report.baseline_status = baseline.status
        report.baseline_signal = bsignal.kind
        report.vendor = bsignal.vendor

        if not baseline.error:
            self.memory.record("baseline", signal=bsignal.kind, status=baseline.status,
                               improved=False, evidence=baseline.transcript_id)

        if not bsignal.is_blocked and not explore:
            report.stopped_reason = (
                f"baseline {baseline.status} {bsignal.kind} — tidak terblokir, tidak perlu bypass"
            )
            return report

        round_limit = max_rounds if max_rounds > 0 else 10**6
        base_req = seed
        base_obs = baseline
        stale_rounds = 0

        for round_no in range(1, round_limit + 1):
            report.rounds = round_no
            signal = base_obs.signal(baseline_status=baseline.status)
            moves = propose(base_req, signal_kind=signal.kind, vendor=signal.vendor,
                            memory=self.memory, limit=max(1, moves_per_round))
            if not moves:
                report.stopped_reason = "tidak ada mutasi baru"
                break
            round_best: tuple[Move, Observation, AdaptiveAttempt] | None = None
            for move in moves:
                if progress:
                    progress("adaptive_strategy", {
                        "target": self.target, "round": round_no, "strategy": move.strategy,
                        "url": move.request.url, "score": move.score,
                    })
                obs = self._execute(probe, move, round_no=round_no)
                attempt = self._attempt(move, obs, base_obs, round_no)
                self._record(attempt)
                report.attempts.append(attempt)
                if progress:
                    progress("adaptive_result", attempt.to_dict())
                if attempt.improved:
                    round_best = (move, obs, attempt)
                    break
            if round_best is None:
                stale_rounds += 1
                if stale_rounds >= 2:
                    report.stopped_reason = "dua putaran tanpa perbaikan"
                    break
                continue
            stale_rounds = 0
            move, obs, attempt = round_best
            report.wins += 1
            report.best_strategy = move.strategy
            if progress:
                progress("adaptive_win", attempt.to_dict())
            if stop_on_success:
                report.stopped_reason = f"bypass via {move.strategy}"
                break
            base_req = move.request
            base_obs = obs
        else:
            report.stopped_reason = report.stopped_reason or "batas putaran tercapai"

        if not report.stopped_reason:
            report.stopped_reason = "selesai"
        self.memory.save()
        return report


def format_report(report: AdaptiveReport) -> str:
    head = (
        f"## Adaptive — {report.target}\n"
        f"seed: `{report.seed}`\n"
        f"baseline: {report.baseline_status or '-'} {report.baseline_signal}"
        f"{' · ' + report.vendor if report.vendor else ''}\n"
        f"putaran: {report.rounds} · percobaan: {len(report.attempts)} · "
        f"bypass: {'ya' if report.bypassed else 'belum'}"
        f"{' (' + report.best_strategy + ')' if report.best_strategy else ''}\n"
        f"berhenti: {report.stopped_reason}\n"
        f"memori: `{report.memory_path}`"
    )
    lines = [head, ""]
    for i, a in enumerate(report.attempts, 1):
        mark = "✓" if a.improved else "·"
        tx = f" tx={a.transcript_id}" if a.transcript_id else ""
        lines.append(
            f"{i}. {mark} r{a.round} `{a.strategy}` → {a.status or '-'} {a.signal}"
            f"{' improved' if a.improved else ''}{tx}"
        )
        if a.note:
            lines.append(f"    {a.note[:200]}")
    if not report.attempts:
        lines.append("(tidak ada mutasi yang dijalankan)")
    return "\n".join(lines)
