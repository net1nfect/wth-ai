"""Temuan SAST masuk sebagai candidate; gate veracity tidak boleh menaikkannya."""

from __future__ import annotations

from wth_ai.findings_db import FindingsDB
from wth_ai.models import FindingState
from wth_ai.redteam.models import AttemptState
from wth_ai.sast.finding_map import to_finding
from wth_ai.verify_gate import apply_verification

RAW = {
    "rule_id": "wth.python.sql-string-concat",
    "path": "app.py",
    "line": 12,
    "severity": "ERROR",
    "message": "query SQL digabung dengan string",
    "snippet": "query = 'SELECT * FROM t WHERE id=' + uid",
}


def test_static_finding_is_candidate_with_static_origin() -> None:
    finding = to_finding(RAW, target_name="proj")
    assert finding.state is AttemptState.CANDIDATE
    assert finding.origin == "static"
    assert finding.provenance == "SAST_OPENGREP"
    assert finding.vuln_class == "wth.python.sql-string-concat"
    assert finding.severity == "high"
    assert any("static:opengrep" in item for item in finding.evidence)


def test_static_finding_id_is_stable() -> None:
    first = to_finding(RAW, target_name="proj")
    second = to_finding(dict(RAW), target_name="proj")
    assert first.id == second.id


def test_static_finding_severity_mapping() -> None:
    low = to_finding({**RAW, "severity": "INFO"}, target_name="proj")
    assert low.severity == "low"


def test_verdict_verified_cannot_promote_static_finding() -> None:
    """Temuan statis tidak punya request/response, jadi tetap candidate.

    Ini bukan pengecualian yang ditambahkan di sini — `decide_state` sudah
    menahannya karena tidak ada bukti runtime. Test ini mengunci perilaku itu
    supaya fitur SAST tidak diam-diam membuka jalan ke VERIFIED.
    """
    db = FindingsDB()
    finding = db.add_finding(
        {
            "id": "F-S1",
            "title": "SQL concat di app.py:12",
            "severity": "high",
            "state": "candidate",
            "evidence": ["static:opengrep:wth.python.sql-string-concat:app.py:12"],
        }
    )
    state = apply_verification(db, finding, {"verdict": "verified"})
    assert state is FindingState.CANDIDATE
