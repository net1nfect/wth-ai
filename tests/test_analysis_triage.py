"""Fase 6 P6 tests: response analysis, evidence bundles, triage."""

from __future__ import annotations

from wth_ai.analysis import analyze_exec_results, detect_verbose_error
from wth_ai.analysis.response_engine import ResponseAnalyzer
from wth_ai.evidence import build_bundle
from wth_ai.triage import TriageEngine


# --- response analysis ---
def test_detect_verbose_error_signatures():
    assert detect_verbose_error("Traceback (most recent call last):") == "stack_trace"
    assert detect_verbose_error("You have an error in your SQL syntax") == "sql_error"
    assert detect_verbose_error("just fine") is None


def test_response_diff():
    diff = ResponseAnalyzer().compare(
        {"status": 200, "length": 100, "headers": {"A": "1"}},
        {"status": 403, "length": 50, "headers": {"A": "1", "B": "2"}},
    )
    assert diff.status_changed
    assert diff.length_delta == -50
    assert "b" in diff.header_diff


def test_analyze_exec_results_finds_verbose_error():
    results = [{
        "target_url": "https://example.com/x", "module": "http_probe",
        "observation": {"body_excerpt": "PHP Fatal error: <b>Fatal error</b>: boom",
                        "response": {"status": 200}},
    }]
    findings = analyze_exec_results(results)
    assert any(f["category"] == "info_disclosure" for f in findings)


def test_analyze_exec_results_server_error():
    results = [{"target_url": "https://example.com/x", "module": "http_probe",
                "observation": {"response": {"status": 500}}}]
    findings = analyze_exec_results(results)
    assert any(f["category"] == "anomaly" for f in findings)


# --- evidence bundle ---
def test_evidence_bundle_fields_and_hash():
    finding = {
        "title": "HIPOTESIS: Weak headers", "asset": "https://example.com/",
        "category": "headers", "severity": "low", "confidence": 0.6,
        "evidence": {"request": {"method": "GET", "url": "https://example.com/"},
                     "response": {"status": 200, "length": 1234, "server": "nginx"},
                     "body_sha256": "abc", "missing_headers": ["Content-Security-Policy"]},
    }
    b = build_bundle(finding, graph_node_ref="https://example.com/").to_dict()
    assert b["request_metadata"]["method"] == "GET"
    assert b["body_hash"] == "abc"
    assert b["severity_candidate"] == "low"
    assert b["reproduction_steps"]
    assert len(b["artifact_sha256"]) == 64  # sha256 hex


# --- triage ---
def _findings():
    return [
        {"title": "HIPOTESIS: Weak headers", "asset": "https://example.com/",
         "category": "headers", "severity": "low", "confidence": 0.6,
         "evidence": {"missing_headers": ["CSP"]}},
        {"title": "HIPOTESIS: Weak headers", "asset": "https://example.com/",
         "category": "headers", "severity": "low", "confidence": 0.9,
         "evidence": {"missing_headers": ["CSP"]}},  # duplicate, higher conf
        {"title": "HIPOTESIS: Broken access control", "asset": "https://admin.example.com/account",
         "category": "access_control", "severity": "high", "confidence": 0.5,
         "evidence": {"anon": {"status": 200}}},
        {"title": "HIPOTESIS: out of scope", "asset": "https://evil.com/x",
         "category": "anomaly", "severity": "medium", "confidence": 0.5, "evidence": {"x": 1}},
    ]


def test_triage_dedups_and_filters_scope():
    out = TriageEngine(scope_hosts={"example.com"}).triage(_findings())
    titles = [f["title"] for f in out["findings"]]
    # duplicate headers collapsed to one; out-of-scope dropped
    assert titles.count("HIPOTESIS: Weak headers") == 1
    assert all("evil.com" not in f["asset"] for f in out["findings"])
    assert out["dropped_out_of_scope"] >= 1


def test_triage_attaches_fix_and_evidence():
    out = TriageEngine(scope_hosts={"example.com"}).triage(_findings())
    ac = next(f for f in out["findings"] if f["category"] == "access_control")
    assert "authorization" in ac["fix_recommendation"].lower()
    assert ac["evidence_bundle"]["artifact_sha256"]
    assert ac["retest_required"] is True


def test_triage_emits_high_priority_event():
    from wth_ai.events import EventBus
    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.type))
    TriageEngine(scope_hosts={"example.com"}).triage(_findings(), bus=bus, job_id="J1")
    assert "finding.high_priority" in seen


def test_triage_downgrades_unsupported_medium():
    f = [{"title": "claim", "asset": "https://example.com/", "category": "anomaly",
          "severity": "high", "confidence": 0.5, "evidence": {}}]
    out = TriageEngine(scope_hosts={"example.com"}).triage(f)
    assert out["findings"][0]["severity"] == "low"  # no evidence -> downgraded
