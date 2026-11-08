"""Telemetry opt-in: hanya agregat, tidak pernah data target/temuan/secret.

Empat kunci payload (`runs`, `durations`, `exit_codes`, `version`) adalah
seluruh kontrak; test ini yang menjaganya, bukan assert runtime.
"""

from __future__ import annotations

import json

from wth_ai.telemetry.aggregate import build_report

RUNS = [
    {"run_id": "r1", "started_at": "2026-11-08T01:00:00Z", "duration_s": 12.5,
     "exit_code": 0, "target": "https://internal.example.corp",
     "findings": [{"id": "F-001", "hypothesis": "BOLA"}],
     "token": "sk-live-abcdef0123456789"},
    {"run_id": "r2", "duration_s": 3.0, "exit_code": 1,
     "host": "10.0.0.7", "secret": "AKIAIOSFODNN7EXAMPLE"},
]

def test_payload_has_only_aggregate_keys() -> None:
    report = build_report(RUNS)
    assert set(report) == {"runs", "durations", "exit_codes", "version"}
    assert report["runs"] == 2
    assert report["durations"] == [12.5, 3.0]
    assert report["exit_codes"] == {"0": 1, "1": 1}

def test_no_target_findings_or_secret_ever_appears() -> None:
    blob = json.dumps(build_report(RUNS))
    for forbidden in ("internal.example.corp", "10.0.0.7", "F-001", "BOLA",
                      "sk-live", "AKIA", "token", "secret", "host", "target"):
        assert forbidden not in blob, f"{forbidden!r} bocor ke payload telemetry"

def test_empty_input_is_safe() -> None:
    report = build_report([])
    assert report["runs"] == 0
    assert report["durations"] == []
    assert report["exit_codes"] == {}

def test_bad_values_are_ignored_not_raised() -> None:
    report = build_report([
        {"duration_s": "bukan-angka", "exit_code": None},
        {"duration_s": None, "exit_code": "boom"},
        {"duration_s": 1, "exit_code": 2},
    ])
    assert report["durations"] == [1.0]
    assert report["exit_codes"] == {"2": 1}

def test_version_is_a_string() -> None:
    assert isinstance(build_report([])["version"], str)
