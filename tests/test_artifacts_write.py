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


def test_chains_written_when_provided(tmp_path):
    """B7: rantai muncul sebagai `chains.json` di artefak (bukan hanya memori)."""
    from wth_ai.redteam.chain import Chain

    chains = [Chain(id="C-001", root="F-001", links=[{"finding": "F-001", "context": {"credentials": "x"}}])]
    write_findings(tmp_path, _findings(), chains=chains)
    path = tmp_path / "chains.json"
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data[0]["id"] == "C-001"
    assert data[0]["root"] == "F-001"


def test_no_chains_file_when_none(tmp_path):
    write_findings(tmp_path, _findings())
    assert not (tmp_path / "chains.json").exists()