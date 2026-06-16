"""Response Analyzer (flowchart node 14).

Pure, deterministic, offline analysis of HTTP responses into typed signals:
status/header/body diff, timing anomaly, reflection, auth-boundary, error
pattern. Operates on already-captured responses (no network), so it is reusable
by the executor and fully unit-testable. It complements `probe.diff_brain`
(which performs the live fetching) by turning raw observations into structured,
confidence-scored evidence signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Error/leak fingerprints (verbose error -> grounded signal).
_ERROR_PATTERNS = re.compile(
    r"(sql syntax|mysql|psql|postgres|ora-\d+|sqlite|odbc|stack trace|traceback "
    r"\(most recent call last\)|exception in thread|warning: |fatal error|"
    r"undefined index|nullpointerexception|500 internal server error)",
    re.IGNORECASE,
)
_SECURITY_HEADERS = (
    "content-security-policy",
    "x-frame-options",
    "strict-transport-security",
    "x-content-type-options",
)


@dataclass
class ResponseObservation:
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    elapsed_ms: float = 0.0

    def header(self, name: str) -> str:
        low = name.lower()
        for k, v in self.headers.items():
            if k.lower() == low:
                return v
        return ""


@dataclass
class AnalysisSignal:
    signal_type: str
    confidence: float          # 0..1
    detail: str
    vuln_hint: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "confidence": round(self.confidence, 2),
            "detail": self.detail,
            "vuln_hint": self.vuln_hint,
        }


# Map a diff-brain style signal name to (vuln_hint, base confidence).
_PROBE_SIGNAL_MAP = {
    "xss_reflection": ("xss", 0.8),
    "idor_diff": ("idor", 0.7),
    "sqli_error": ("sqli", 0.75),
    "status_change": ("unknown", 0.35),
    "length_change": ("unknown", 0.3),
}


class ResponseAnalyzer:
    """Turn response observations into confidence-scored analysis signals."""

    def __init__(self, *, timing_threshold_ms: float = 1500.0) -> None:
        self.timing_threshold_ms = timing_threshold_ms

    def analyze_single(self, resp: ResponseObservation, *, marker: str = "") -> list[AnalysisSignal]:
        signals: list[AnalysisSignal] = []
        if marker and marker in resp.body:
            signals.append(AnalysisSignal("reflection", 0.7, f"marker '{marker}' reflected", "xss"))
        if _ERROR_PATTERNS.search(resp.body):
            signals.append(AnalysisSignal("error_pattern", 0.6, "verbose error / stack pattern", "info_leak"))
        missing = [h for h in _SECURITY_HEADERS if not resp.header(h)]
        if missing:
            signals.append(AnalysisSignal(
                "missing_security_headers", 0.3,
                "missing: " + ", ".join(missing), "misconfig",
            ))
        if resp.status >= 500:
            signals.append(AnalysisSignal("server_error", 0.4, f"status {resp.status}", "info_leak"))
        return signals

    def analyze_pair(
        self,
        baseline: ResponseObservation,
        mutated: ResponseObservation,
        *,
        mutation: str = "",
        marker: str = "",
    ) -> list[AnalysisSignal]:
        signals: list[AnalysisSignal] = []

        # Status diff -> auth-boundary / behavioural change.
        if baseline.status != mutated.status:
            auth_pair = {baseline.status, mutated.status}
            if auth_pair & {401, 403} and (200 in auth_pair):
                signals.append(AnalysisSignal(
                    "auth_boundary", 0.65,
                    f"status {baseline.status} -> {mutated.status}", "access_control",
                ))
            else:
                signals.append(AnalysisSignal(
                    "status_change", 0.4,
                    f"status {baseline.status} -> {mutated.status}", "unknown",
                ))

        # Body length diff.
        length_delta = abs(len(mutated.body) - len(baseline.body))
        if length_delta > 200:
            signals.append(AnalysisSignal(
                "body_diff", 0.4, f"length delta {length_delta}", "unknown",
            ))

        # Reflection (XSS marker).
        if marker and marker in mutated.body and marker not in baseline.body:
            signals.append(AnalysisSignal("reflection", 0.8, f"marker '{marker}' newly reflected", "xss"))

        # Error pattern introduced by mutation (SQLi etc.).
        if _ERROR_PATTERNS.search(mutated.body) and not _ERROR_PATTERNS.search(baseline.body):
            signals.append(AnalysisSignal("error_pattern", 0.7, "error pattern after mutation", "sqli"))

        # Timing anomaly (time-based injection clue).
        if mutated.elapsed_ms - baseline.elapsed_ms >= self.timing_threshold_ms:
            signals.append(AnalysisSignal(
                "timing_anomaly", 0.55,
                f"+{mutated.elapsed_ms - baseline.elapsed_ms:.0f}ms vs baseline", "sqli",
            ))
        return signals

    def from_probe(self, probe: dict[str, Any]) -> list[AnalysisSignal]:
        """Interpret a diff-brain probe dict into typed signals."""
        signal_name = probe.get("signal", "")
        vuln_hint, base = _PROBE_SIGNAL_MAP.get(signal_name, ("unknown", 0.3))
        # Anomaly score (if present) reinforces confidence.
        anomaly = float(probe.get("anomaly_score", 0.0) or 0.0)
        confidence = max(base, min(0.95, base * 0.5 + anomaly * 0.6))
        detail = signal_name or "probe signal"
        out = [AnalysisSignal(signal_name or "probe", round(confidence, 2), detail, vuln_hint)]
        if probe.get("status_delta", 0):
            out.append(AnalysisSignal("status_change", 0.35, f"status delta {probe['status_delta']}", vuln_hint))
        return out

    @staticmethod
    def aggregate_confidence(signals: list[AnalysisSignal]) -> float:
        """Combine independent signals (noisy-OR)."""
        if not signals:
            return 0.0
        prod = 1.0
        for s in signals:
            prod *= (1.0 - max(0.0, min(1.0, s.confidence)))
        return round(1.0 - prod, 2)
