"""Laporan HTML statis: mandiri, memuat hash, tanpa script eksternal."""

from wth_ai.reporting.html import render_html, write_html
from wth_ai.redteam.bundle import write_bundle
from wth_ai.redteam.models import Finding


def _bundle(tmp_path):
    out = tmp_path / "bundle"
    write_bundle(out, findings=[Finding(id="F-1", hypothesis="BOLA")],
                 scope_card={"target": "lab"}, audit_events=[{"event": "x"}])
    return out


def test_html_is_self_contained(tmp_path):
    doc = render_html(_bundle(tmp_path))
    assert "<script src=" not in doc
    assert "http://" not in doc.split("</style>")[0]


def test_html_contains_findings_and_root_hash(tmp_path):
    out = _bundle(tmp_path)
    path = write_html(out, tmp_path / "report.html")
    text = path.read_text()
    assert "F-1" in text
    assert (out / "root_hash.txt").read_text().strip() in text