"""WTH-AI analysis layer — response analysis + finding triage.

  - response_analyzer : ResponseAnalyzer (flowchart 14) — typed, confidence-scored
    signals from captured HTTP responses (status/header/body diff, reflection,
    timing, error patterns, auth boundary).
  - triage            : TriageEngine (flowchart 16) — dedup/merge, severity
    candidate, TTST-aware false-positive filter, human-review queue.

Safety: analysis/triage only — no network, no execution, no permission changes.
"""

from __future__ import annotations

from wth_ai.analysis.response_analyzer import (
    AnalysisSignal,
    ResponseAnalyzer,
    ResponseObservation,
)
from wth_ai.analysis.triage import TriageEngine, TriageReport, TriagedFinding

__all__ = [
    "ResponseAnalyzer",
    "ResponseObservation",
    "AnalysisSignal",
    "TriageEngine",
    "TriageReport",
    "TriagedFinding",
]
