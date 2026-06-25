"""Fase 6 P7 tests: unified reporting + continuous loop."""

from __future__ import annotations

import json
from pathlib import Path

from wth_ai.continuous import build_retest_queue, delta_report
from wth_ai.reporting import ReportBuilder, build_report


def _make_job_dir(tmp_path: Path) -> Path:
    job = tmp_path / "JOB-rep"
    norm = job / "normalized"
    norm.mkdir(parents=True)
    (job / "recon_summary.json").write_text(json.dumps({
        "target": "example.com",
        "assets": {"subdomains": 3, "urls": 10, "endpoints": 4},
    }), encoding="utf-8")
    (norm / "asset_graph.json").write_text(json.dumps({
        "node_count": 12, "edge_count": 9, "node_types": {"domain": 1, "url": 10},
        "clusters": [{"cluster": "example.com", "size": 11}],
    }), encoding="utf-8")
    (norm / "risk_heatmap.json").write_text(json.dumps({
        "critical": [], "high": [{"asset": "https://admin.example.com"}],
        "medium": [], "low": [], "info": [],
    }), encoding="utf-8")
    (job / "priority_targets.json").write_text(json.dumps([
        {"asset": "https://admin.example.com", "score": 9, "severity": "high",
         "reasons": ["keyword:admin", "auth-surface:403"]},
    ]), encoding="utf-8")
    (job / "triage_findings.json").write_text(json.dumps({
        "count": 1, "severity_counts": {"high": 1},
        "findings": [{
            "finding_id": "FND-1", "title": "HIPOTESIS: Broken access control",
            "asset": "https://admin.example.com/account", "category": "access_control",
            "severity": "high", "confidence": 0.5, "detail": "anon==auth",
            "fix_recommendation": "Enforce server-side authorization.",
            "retest_required": True, "proven": False,
            "evidence_bundle": {"bundle_id": "EVB-1", "artifact_sha256": "a" * 64},
        }],
    }), encoding="utf-8")
    (job / "hypotheses.json").write_text(json.dumps([
        {"title": "HIPOTESIS: IDOR", "asset": "https://admin.example.com", "confidence": 0.6},
    ]), encoding="utf-8")
    return job


def test_report_build_all_formats(tmp_path):
    job = _make_job_dir(tmp_path)
    written = build_report(job, target="example.com")
    assert set(written) == {"json", "md", "html"}
    for p in written.values():
        assert Path(p).exists()
    md = (job / "report.md").read_text()
    assert "Executive Summary" in md
    assert "Risk Heatmap" in md
    assert "Broken access control" in md
    assert "Retest Checklist" in md
    html = (job / "report.html").read_text()
    assert "<html>" in html and "example.com" in html


def test_report_gather_severity(tmp_path):
    job = _make_job_dir(tmp_path)
    r = ReportBuilder(job, target="example.com").gather()
    assert r["severity_counts"] == {"high": 1}
    assert r["asset_graph"]["node_count"] == 12
    assert len(r["findings"]) == 1


def test_delta_report_detects_new():
    old = {"http_probes": [{"url": "https://example.com/a"}]}
    new = {"http_probes": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}]}
    out = delta_report(old, new)
    assert out["changes"]["changed"]
    assert "https://example.com/b" in out["markdown"]


def test_delta_report_no_change():
    same = {"http_probes": [{"url": "https://example.com/a"}]}
    out = delta_report(same, same)
    assert not out["changes"]["changed"]
    assert "No changes" in out["markdown"]


def test_build_retest_queue():
    findings = [
        {"finding_id": "F1", "asset": "https://x.com/a", "category": "headers", "retest_required": True},
        {"finding_id": "F2", "asset": "https://x.com/b", "category": "api", "retest_required": False},
        {"finding_id": "F3", "asset": "https://x.com/c", "category": "reflection", "retest_required": True},
    ]
    q = build_retest_queue(findings)
    ids = {item["finding_id"] for item in q}
    assert ids == {"F1", "F3"}
    modules = {item["module"] for item in q}
    assert "header_review" in modules and "param_probe" in modules
