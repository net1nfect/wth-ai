"""SARIF 2.1.0: hanya CONFIRMED, level mengikuti severity."""

import json

from wth_ai.reporting.sarif import build_sarif
from wth_ai.redteam.models import AttemptState, Finding


def _confirmed(severity="high"):
    f = Finding(id="F-1", hypothesis="BOLA", vuln_class="bola", severity=severity)
    f.transition(AttemptState.ATTEMPTING)
    f.transition(AttemptState.CONFIRMED)
    return f


def test_version_and_schema():
    doc = build_sarif([])
    assert doc["version"] == "2.1.0"
    assert doc["$schema"].endswith("sarif-2.1.0.json")


def test_only_confirmed_results():
    doc = build_sarif([_confirmed(), Finding(id="F-2", hypothesis="candidate")])
    assert len(doc["runs"][0]["results"]) == 1


def test_severity_maps_to_level():
    doc = build_sarif([_confirmed("high")])
    assert doc["runs"][0]["results"][0]["level"] == "error"


def test_output_is_json_serialisable():
    json.dumps(build_sarif([_confirmed("medium")]))