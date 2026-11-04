"""Laporan Bahasa Indonesia: Ringkasan Pimpinan + rujukan regulasi + disclaimer."""

import json
import os
import subprocess
import sys
from pathlib import Path

from wth_ai.redteam.models import AttemptState, Finding
from wth_ai.reporting.id_report import (DEFAULT_REGULATION, DISCLAIMER,
                                        load_map, render_id_report)

REPO = Path(__file__).resolve().parents[1]


def _findings():
    return [
        Finding(id="F-001", hypothesis="BOLA pada /api/v1/orders",
                state=AttemptState.CONFIRMED,
                severity="high", vuln_class="access_control", target="http://lab"),
        Finding(id="F-002", hypothesis="Swagger terbuka",
                state=AttemptState.CANDIDATE,
                severity="medium", vuln_class="information_disclosure", target="http://lab"),
    ]


def test_report_has_executive_summary_and_regulation():
    text = render_id_report(_findings(), run_meta={"scan_mode": "deep",
                                                   "status": "completed"})
    assert "## Ringkasan Pimpinan" in text
    assert "Peraturan BSSN" in text
    assert "Total temuan: 2 (confirmed 1)." in text
    assert "Mode pemindaian: deep." in text


def test_only_confirmed_listed_as_confirmed():
    text = render_id_report(_findings())
    confirmed_block = text.split("## Temuan Terkonfirmasi")[1].split("##")[0]
    assert "F-001" in confirmed_block
    assert "F-002" not in confirmed_block


def test_candidate_never_described_as_confirmed():
    text = render_id_report([_findings()[1]])
    assert "Total temuan: 1 (confirmed 0)." in text
    assert "| F-002" in text
    assert "CANDIDATE" in text


def test_disclaimer_present():
    assert DISCLAIMER in render_id_report(_findings())


def test_map_file_is_valid_and_has_no_article_numbers():
    mapping = load_map()
    assert mapping, "data/kb/bssn_map.json harus ada"
    for value in mapping.values():
        assert "Peraturan BSSN" in value
        assert "pasal" not in value.lower()


def test_missing_map_falls_back_to_default(tmp_path):
    text = render_id_report(_findings(), regulation_map={})
    assert DEFAULT_REGULATION in text


def test_cli_writes_report_id(tmp_path):
    findings = tmp_path / "out" / "bundle"
    findings.mkdir(parents=True)
    (findings / "findings.json").write_text(
        json.dumps([finding.to_dict() for finding in _findings()]), encoding="utf-8")
    (tmp_path / "out" / "run.json").write_text(json.dumps({"scan_mode": "quick"}),
                                               encoding="utf-8")
    env = {**os.environ, "WTH_HOME": str(tmp_path / "home"), "WTH_SKIP_SETUP": "1",
           "WTH_PLAIN": "1"}
    proc = subprocess.run(
        [sys.executable, str(REPO / "wth-main.py"), "report", "--bundle", "out/bundle",
         "--format", "id"],
        cwd=tmp_path, capture_output=True, text=True, timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr
    path = tmp_path / "out" / "report-id.md"
    assert path.is_file()
    body = path.read_text()
    assert "Ringkasan Pimpinan" in body
    assert "Peraturan BSSN 1/2024" in body or "Peraturan BSSN" in body