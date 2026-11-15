"""Runner pipeline: baseline → rencana probe → scheduler berbiaya → verdict.

`execute_pipeline` (adapter executor) dan tool AI memakai runner yang sama.
Technique hanya menyusun probe; session yang mengirim; runner yang memutuskan
kapan berhenti. Dengan begitu budget/early-stop tidak diduplikasi di executor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from wth_ai.pipeline.models import ProbeOutcome, Surface, TechniqueResult
from wth_ai.pipeline.session import ProbeSession
from wth_ai.pipeline.techniques import Technique, get_technique
from wth_ai.state.normalizer import Observation


@dataclass
class PipelineRun:
    surface: Surface
    technique: Technique
    baseline: Observation
    baseline_body: str = ""
    outcomes: list[ProbeOutcome] = field(default_factory=list)
    skipped: int = 0
    elapsed_ms: float = 0.0
    result: TechniqueResult = field(default_factory=TechniqueResult)

    @property
    def pairs(self) -> list[dict[str, Any]]:
        baseline = {
            "method": self.baseline.method,
            "url": self.baseline.url,
            "response_status": self.baseline.status,
            "body_snippet": self.baseline_body[:4000],
            "response_headers": dict(self.baseline.headers),
            "role": "baseline",
            "elapsed_ms": self.baseline.elapsed_ms,
            "transcript_id": self.baseline.transcript_id,
        }
        return [baseline, *(o.pair for o in self.outcomes)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.surface.url,
            "vuln_class": self.technique.vuln_class,
            "method": self.surface.method,
            "params": [p.to_dict() for p in self.surface.params],
            "baseline": {
                "status": self.baseline.status,
                "url": self.baseline.url,
                "body_length": self.baseline.body_length,
                "elapsed_ms": round(self.baseline.elapsed_ms, 1),
                "transcript_id": self.baseline.transcript_id,
            },
            "probes": [
                {
                    **outcome.to_dict(),
                    "elapsed_ms": round(outcome.observation.elapsed_ms, 1),
                    "transcript_id": outcome.observation.transcript_id,
                }
                for outcome in self.outcomes
            ],
            "skipped": self.skipped,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "verified": self.result.verified,
            "risk": self.result.risk,
            "evidence": list(self.result.evidence),
            "observations": list(self.result.observations),
            "verdicts": list(self.result.verdicts),
        }


def _default_budget(technique: Technique) -> int:
    return int(getattr(technique, "probe_budget", 12) or 12)


def run_technique(
    technique: Technique,
    surface: Surface,
    session: ProbeSession,
    *,
    max_probes: int | None = None,
    deadline_s: float | None = None,
    context: dict[str, Any] | None = None,
    on_probe: Callable[[ProbeOutcome], None] | None = None,
    baseline: Observation | None = None,
    baseline_body: str | None = None,
) -> PipelineRun:
    """Jalankan satu technique dengan budget + early-stop.

    Baseline opsional supaya caller bisa memakai baseline yang sudah ada
    (mis. reference probe SSRF) tanpa request ulang.
    """
    started = time.perf_counter()
    if baseline is None:
        baseline_factory = getattr(technique, "baseline_request", None)
        baseline_request = baseline_factory(surface, context=context) if callable(baseline_factory) else None
        if baseline_request is not None:
            baseline, base_body = session.send(baseline_request, role="baseline")
        else:
            baseline, base_body = session.baseline(surface)
    else:
        base_body = baseline_body or ""
    run = PipelineRun(surface=surface, technique=technique, baseline=baseline,
                      baseline_body=base_body)
    if baseline.status == 0:
        run.elapsed_ms = (time.perf_counter() - started) * 1000.0
        return run

    plans = technique.plans(surface, baseline, base_body, context=context)
    budget = max(1, int(max_probes if max_probes is not None else _default_budget(technique)))
    custom = getattr(technique, "run_probes", None)
    if callable(custom):
        run.outcomes = list(custom(
            session, surface, baseline, plans,
            context=context, budget=budget, deadline_s=deadline_s, started=started,
        ))
        run.skipped = max(0, len(plans) - len(run.outcomes))
        if on_probe is not None:
            for outcome in run.outcomes:
                on_probe(outcome)
    else:
        for index, plan in enumerate(plans):
            if index >= budget:
                run.skipped = len(plans) - index
                break
            if deadline_s is not None and (time.perf_counter() - started) >= deadline_s:
                run.skipped = len(plans) - index
                break
            outcome = session.probe(plan, baseline)
            run.outcomes.append(outcome)
            if on_probe is not None:
                on_probe(outcome)
            if technique.early_verified(baseline, base_body, outcome):
                run.skipped = max(0, len(plans) - index - 1)
                break

    run.result = technique.interpret(surface, baseline, base_body, run.outcomes, context=context)
    run.elapsed_ms = (time.perf_counter() - started) * 1000.0
    return run


def run_class(
    vuln_class: str,
    surface: Surface,
    session: ProbeSession,
    **kwargs: Any,
) -> PipelineRun | None:
    technique = get_technique(vuln_class)
    if technique is None:
        return None
    context = dict(kwargs.pop("context", {}) or {})
    context.setdefault("vuln_class", vuln_class)
    return run_technique(technique, surface, session, context=context, **kwargs)
