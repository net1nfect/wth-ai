"""Artefak: satu md per temuan + indeks json/csv."""

import csv
import json

from wth_ai.redteam.artifacts_write import write_findings
from wth_ai.redteam.models import AttemptState, Finding


def _findings():
    confirmed = Finding(id="F-001", hypothesis="BOLA /orders", severity="high")
    confirmed.transition(AttemptState.ATTEMPTING)
    confirmed.transition(AttemptState.CONFIRMED)
    return [confirmed, Finding(id="F-002", hypothesis="GraphQL")]


def test_one_markdown_per_finding(tmp_path):
    write_findings(tmp_path, _findings())
    assert (tmp_path / "vulnerabilities" / "F-001.md").is_file()
    assert (tmp_path / "vulnerabilities" / "F-002.md").is_file()
    assert "confirmed" in (tmp_path / "vulnerabilities" / "F-001.md").read_text()


def test_json_index_is_loadable(tmp_path):
    write_findings(tmp_path, _findings())
    data = json.loads((tmp_path / "vulnerabilities.json").read_text())
    assert [row["id"] for row in data] == ["F-001", "F-002"]


def test_csv_has_header_and_rows(tmp_path):
    write_findings(tmp_path, _findings())
    with (tmp_path / "vulnerabilities.csv").open(newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["id", "severity", "state", "provenance", "hypothesis"]
    assert len(rows) == 3