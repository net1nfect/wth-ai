"""Finding Correlation & Triage (flowchart node 16 / J).

Deterministic, offline triage over raw finding candidates:
  - normalise heterogeneous candidate shapes (diff-brain probes, planner
    FINDING_CANDIDATE blocks, FindingsDB findings),
  - deduplicate + merge related findings (same vuln class + component),
  - derive a severity candidate (vuln base severity x evidence confidence),
  - false-positive filter (TTST-aware: claims with no [TX:]/[EV:] transcript and
    weak signals are not promoted),
  - build a human-review queue.

Aligns with AGENT.md Evidence Law: nothing is asserted as verified without
grounded transcript/evidence; uncertain items go to review, not to the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from wth_ai.analysis.response_analyzer import ResponseAnalyzer
from wth_ai.cognition import signals as cog_signals

_TX_RE = re.compile(r"\[(TX|EV):[^\]]+\]")
_SEV_TO_CONF = {"critical": 0.9, "high": 0.75, "medium": 0.55, "low": 0.35, "info": 0.2}


def _norm_component(text: str) -> str:
    t = (text or "").strip().lower()
    return t.split("?")[0].rstrip("/")


def _infer_vuln_class(cand: dict[str, Any]) -> str:
    if cand.get("vuln_class"):
        return cog_signals.normalize_vuln_class(cand["vuln_class"])
    signal = (cand.get("signal") or "").lower()
    mapping = {
        "xss_reflection": "xss", "reflection": "xss",
        "idor_diff": "idor", "sqli_error": "sqli",
        "auth_boundary": "idor", "timing_anomaly": "sqli",
    }
    if signal in mapping:
        return mapping[signal]
    title = (cand.get("title") or "").lower()
    for vc in ("idor", "bola", "ssrf", "xss", "sqli", "rce", "xxe", "csrf", "auth"):
        if vc in title:
            return vc
    return "unknown"


def _has_transcript(evidence: list[str]) -> bool:
    return any(_TX_RE.search(str(e)) for e in evidence)


def _severity_band(score: float) -> str:
    if score >= 0.8:
        return "critical"
    if score >= 0.62:
        return "high"
    if score >= 0.42:
        return "medium"
    if score >= 0.25:
        return "low"
    return "info"


@dataclass
class TriagedFinding:
    id: str
    title: str
    vuln_class: str
    component: str
    severity: str
    confidence: float
    state: str                       # candidate | needs_review | likely_false_positive
    evidence: list[str] = field(default_factory=list)
    merged_from: list[str] = field(default_factory=list)
    review_required: bool = False
    false_positive: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "vuln_class": self.vuln_class,
            "component": self.component,
            "severity": self.severity,
            "confidence": round(self.confidence, 2),
            "state": self.state,
            "evidence": self.evidence,
            "merged_from": self.merged_from,
            "review_required": self.review_required,
            "false_positive": self.false_positive,
            "notes": self.notes,
        }


@dataclass
class TriageReport:
    findings: list[TriagedFinding] = field(default_factory=list)
    duplicates_merged: int = 0
    false_positives: int = 0

    def actionable(self) -> list[TriagedFinding]:
        return [f for f in self.findings if not f.false_positive]

    def review_queue(self) -> list[TriagedFinding]:
        return [f for f in self.findings if f.review_required and not f.false_positive]

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.findings),
            "actionable": len(self.actionable()),
            "duplicates_merged": self.duplicates_merged,
            "false_positives": self.false_positives,
            "review_queue": len(self.review_queue()),
            "findings": [f.to_dict() for f in self.findings],
        }

    def format_cli(self) -> str:
        if not self.findings:
            return "## Triage\nTidak ada finding candidate untuk di-triage."
        actionable = self.actionable()
        lines = [
            "## Triage & Correlation",
            f"- Candidates: {len(self.findings)} · actionable: {len(actionable)} · "
            f"merged: {self.duplicates_merged} · likely FP: {self.false_positives} · "
            f"review queue: {len(self.review_queue())}",
            "",
            "### Findings (severity-ordered)",
        ]
        for f in actionable:
            flag = " ⚠ review" if f.review_required else ""
            merged = f" (merged x{len(f.merged_from)})" if f.merged_from else ""
            lines.append(
                f"- [{f.severity.upper()} · conf {int(f.confidence * 100)}%] "
                f"{f.title}{merged}{flag}"
            )
            lines.append(f"  - {f.vuln_class} @ {f.component[:70]} — {f.notes}")
        fps = [f for f in self.findings if f.false_positive]
        if fps:
            lines.append("")
            lines.append("### Filtered (likely false positive)")
            for f in fps:
                lines.append(f"- {f.title} — {f.notes}")
        return "\n".join(lines)


class TriageEngine:
    """Correlate, score, and filter finding candidates."""

    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

    def __init__(self, *, analyzer: ResponseAnalyzer | None = None, fp_confidence: float = 0.35) -> None:
        self.analyzer = analyzer or ResponseAnalyzer()
        self.fp_confidence = fp_confidence

    def triage(self, candidates: list[dict[str, Any]]) -> TriageReport:
        clusters: dict[str, dict[str, Any]] = {}
        merged = 0

        for raw in candidates or []:
            vc = _infer_vuln_class(raw)
            component = raw.get("component") or raw.get("url") or raw.get("affected_asset") or ""
            key = f"{vc}|{_norm_component(component)}"
            evidence = list(raw.get("evidence") or [])
            if raw.get("signal"):
                evidence.append(f"signal:{raw['signal']}")
            confidence = self._candidate_confidence(raw)

            if key in clusters:
                merged += 1
                cl = clusters[key]
                cl["confidence"] = max(cl["confidence"], confidence)
                cl["evidence"] = list(dict.fromkeys(cl["evidence"] + evidence))
                cl["merged_from"].append(raw.get("id") or raw.get("title", "?"))
                if self._SEV_ORDER.get(raw.get("severity", "info"), 5) < self._SEV_ORDER.get(cl["sev_hint"], 5):
                    cl["sev_hint"] = raw.get("severity", "info")
            else:
                clusters[key] = {
                    "title": raw.get("title") or f"{vc} @ {component}",
                    "vuln_class": vc,
                    "component": component,
                    "confidence": confidence,
                    "evidence": evidence,
                    "merged_from": [],
                    "sev_hint": raw.get("severity") or raw.get("severity_hint") or "",
                }

        findings: list[TriagedFinding] = []
        fp_count = 0
        for idx, cl in enumerate(clusters.values(), start=1):
            base_sev = cog_signals.vuln_base_severity(cl["vuln_class"])
            conf = cl["confidence"]
            grounded = _has_transcript(cl["evidence"])
            score = base_sev * (0.45 + 0.55 * conf)
            severity = _severity_band(score)

            false_positive = (not grounded) and conf < self.fp_confidence
            # Unknown class with only weak signal -> noise.
            if cl["vuln_class"] == "unknown" and conf < 0.4 and not grounded:
                false_positive = True
            if false_positive:
                fp_count += 1

            # TTST: high-impact but ungrounded -> must be reviewed, not auto-trusted.
            review = (not false_positive) and (
                (not grounded and conf < 0.7) or severity in {"critical", "high"}
            )
            notes = self._notes(grounded, conf, severity)
            findings.append(TriagedFinding(
                id=f"TR-{idx:03d}",
                title=cl["title"],
                vuln_class=cl["vuln_class"],
                component=cl["component"],
                severity=severity,
                confidence=conf,
                state="likely_false_positive" if false_positive
                else ("needs_review" if review else "candidate"),
                evidence=cl["evidence"][:10],
                merged_from=cl["merged_from"],
                review_required=review,
                false_positive=false_positive,
                notes=notes,
            ))

        findings.sort(key=lambda f: (self._SEV_ORDER.get(f.severity, 5), -f.confidence))
        return TriageReport(findings=findings, duplicates_merged=merged, false_positives=fp_count)

    def _candidate_confidence(self, raw: dict[str, Any]) -> float:
        # Prefer explicit anomaly/probe signals via the analyzer, else severity hint.
        if raw.get("signal") or raw.get("anomaly_score"):
            sigs = self.analyzer.from_probe(raw)
            conf = self.analyzer.aggregate_confidence(sigs)
            if conf:
                return conf
        if "confidence" in raw:
            try:
                return max(0.0, min(1.0, float(raw["confidence"])))
            except (TypeError, ValueError):
                pass
        hint = (raw.get("severity") or raw.get("severity_hint") or "").lower()
        return _SEV_TO_CONF.get(hint, 0.4)

    @staticmethod
    def _notes(grounded: bool, conf: float, severity: str) -> str:
        bits = []
        bits.append("grounded [TX/EV]" if grounded else "no transcript yet")
        bits.append(f"confidence {int(conf * 100)}%")
        if severity in {"critical", "high"} and not grounded:
            bits.append("verify before report (Evidence Law)")
        return "; ".join(bits)
