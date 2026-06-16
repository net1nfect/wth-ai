"""Tests for the WTH-AI analysis layer (response analyzer + triage)."""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.analysis import ResponseAnalyzer, ResponseObservation, TriageEngine
from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


# ---- response analyzer ----

def test_analyzer_detects_reflection_in_pair():
    base = ResponseObservation(status=200, body="<html>hello</html>")
    mut = ResponseObservation(status=200, body="<html>hello WTHMARKER123</html>")
    sigs = ResponseAnalyzer().analyze_pair(base, mut, marker="WTHMARKER123")
    assert any(s.signal_type == "reflection" and s.vuln_hint == "xss" for s in sigs)


def test_analyzer_detects_auth_boundary():
    base = ResponseObservation(status=403, body="forbidden")
    mut = ResponseObservation(status=200, body="secret data here")
    sigs = ResponseAnalyzer().analyze_pair(base, mut)
    assert any(s.signal_type == "auth_boundary" for s in sigs)


def test_analyzer_detects_sql_error_and_timing():
    base = ResponseObservation(status=200, body="ok", elapsed_ms=100)
    mut = ResponseObservation(status=200, body="You have an error in your SQL syntax", elapsed_ms=2000)
    sigs = ResponseAnalyzer().analyze_pair(base, mut)
    types = {s.signal_type for s in sigs}
    assert "error_pattern" in types
    assert "timing_anomaly" in types


def test_analyzer_single_missing_headers_and_error():
    resp = ResponseObservation(status=500, headers={}, body="Traceback (most recent call last):")
    sigs = ResponseAnalyzer().analyze_single(resp)
    types = {s.signal_type for s in sigs}
    assert "missing_security_headers" in types
    assert "error_pattern" in types
    assert "server_error" in types


def test_analyzer_from_probe_and_aggregate():
    az = ResponseAnalyzer()
    sigs = az.from_probe({"signal": "xss_reflection", "anomaly_score": 0.8})
    assert sigs[0].vuln_hint == "xss"
    agg = az.aggregate_confidence(sigs)
    assert 0.0 < agg <= 1.0


# ---- triage engine ----

def test_triage_dedup_and_merge():
    engine = TriageEngine()
    cands = [
        {"title": "IDOR on user_id", "component": "https://x.com/api/o?user_id=1", "vuln_class": "idor",
         "evidence": ["[TX:abc] cross-user data"], "severity": "high"},
        {"title": "IDOR dup", "component": "https://x.com/api/o", "vuln_class": "idor",
         "evidence": ["[TX:def] confirm"], "severity": "high"},
    ]
    report = engine.triage(cands)
    assert report.duplicates_merged == 1
    assert len(report.findings) == 1
    assert report.findings[0].merged_from


def test_triage_false_positive_filter():
    engine = TriageEngine()
    cands = [
        {"title": "weak status blip", "component": "https://x.com/a", "signal": "status_change",
         "anomaly_score": 0.3},
    ]
    report = engine.triage(cands)
    assert report.false_positives >= 1
    assert report.findings[0].false_positive


def test_triage_grounded_high_severity_is_actionable_but_reviewable():
    engine = TriageEngine()
    cands = [
        {"title": "SQLi error confirmed", "component": "https://x.com/p?id=1", "vuln_class": "sqli",
         "evidence": ["[TX:zzz] SQL syntax error"], "severity": "high"},
    ]
    report = engine.triage(cands)
    f = report.findings[0]
    assert not f.false_positive
    assert f.severity in {"high", "critical"}
    assert f in report.actionable()


def test_triage_ungrounded_high_impact_goes_to_review():
    engine = TriageEngine()
    cands = [
        {"title": "Possible RCE", "component": "https://x.com/run", "vuln_class": "rce",
         "evidence": [], "confidence": 0.5},
    ]
    report = engine.triage(cands)
    f = report.findings[0]
    assert f.review_required
    assert f in report.review_queue()


def test_triage_report_serializable_and_formatted():
    engine = TriageEngine()
    report = engine.triage([
        {"title": "IDOR", "component": "https://x.com/api/o", "vuln_class": "idor",
         "evidence": ["[TX:abc]"], "severity": "high"},
    ])
    d = report.to_summary_dict()
    assert d["total"] == 1
    assert "Triage" in report.format_cli()


def test_triage_empty():
    report = TriageEngine().triage([])
    assert report.findings == []
    assert "Triage" in report.format_cli()


# ---- orchestrator integration ----

def test_orchestrator_triage_command():
    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    orch.state.metadata["pre_findings"] = [
        {"title": "xss_reflection on q", "url": "https://x.com/s?q=1", "signal": "xss_reflection",
         "anomaly_score": 0.8, "severity_hint": "high"},
        {"title": "idor_diff on id", "url": "https://x.com/api/o?id=1", "signal": "idor_diff",
         "anomaly_score": 0.7, "severity_hint": "high"},
    ]
    out = orch.handle_input("/triage")
    assert "Triage" in out
    assert orch.state.metadata.get("triage") is not None
    assert orch.state.metadata["triage"]["total"] >= 1


def test_triage_command_registered():
    from wth_ai.slash_commands import SlashCommandHandler
    assert "/triage" in SlashCommandHandler.COMMANDS
