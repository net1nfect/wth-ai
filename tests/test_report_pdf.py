"""PDF tanpa dependency: struktur valid, teks terbaca, halaman bertambah."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from wth_ai.redteam.models import AttemptState, Finding
from wth_ai.reporting.pdf import render_pdf, write_pdf

REPO = Path(__file__).resolve().parents[1]


def _findings(count: int = 2):
    rows = []
    for index in range(count):
        rows.append(Finding(
            id=f"F-{index + 1:03d}", hypothesis=f"BOLA pada /api/v1/orders/{index}",
            state=AttemptState.CONFIRMED if index % 2 == 0 else AttemptState.CANDIDATE,
            severity="high", vuln_class="access_control", target="http://lab"))
    return rows


def _xref_offsets(pdf: bytes) -> list[int]:
    text = pdf.decode("latin-1")
    start = text.index("xref")
    lines = text[start:].split("\n")
    offsets: list[int] = []
    for line in lines[2:]:          # lewati "xref" dan baris "0 N"
        match = re.match(r"^(\d{10}) \d{5} n $", line)
        if match:
            offsets.append(int(match.group(1)))
        elif not line.strip():
            break
    return offsets


def test_pdf_header_and_trailer():
    pdf = render_pdf(_findings())
    assert pdf.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf
    assert b"/Type /Catalog" in pdf
    assert pdf.count(b"/Type /Page ") == 1


def test_pdf_xref_offsets_point_at_objects():
    pdf = render_pdf(_findings())
    offsets = _xref_offsets(pdf)
    assert offsets
    for index, offset in enumerate(offsets, start=1):
        assert pdf[offset:offset + len(f"{index} 0 obj")] == f"{index} 0 obj".encode()


def test_pdf_contains_readable_text():
    pdf = render_pdf(_findings(), meta={"scan_mode": "deep", "root_hash": "abc123"})
    text = pdf.decode("latin-1")
    assert "LAPORAN PENINDAKAN" in text
    assert "F-001" in text
    assert "abc123" in text


def test_pdf_splits_into_pages_when_long():
    pdf = render_pdf(_findings(80))
    assert pdf.count(b"/Type /Page ") >= 2
    assert re.search(rb"/Count ([2-9]\d*)", pdf)


def test_pdf_escapes_parentheses():
    finding = Finding(id="F-001", hypothesis="bukti (req) [x]",
                      state=AttemptState.CONFIRMED, severity="high",
                      vuln_class="bola", target="http://lab")
    text = render_pdf([finding]).decode("latin-1")
    assert r"\(req\)" in text


def test_write_pdf_and_cli_format(tmp_path):
    out = write_pdf(_findings(), tmp_path / "r.pdf")
    assert out.read_bytes().startswith(b"%PDF")

    bundle = tmp_path / "out" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "findings.json").write_text(
        json.dumps([f.to_dict() for f in _findings()]), encoding="utf-8")
    (bundle / "root_hash.txt").write_text("deadbeef\n", encoding="utf-8")
    (tmp_path / "out" / "run.json").write_text(json.dumps({"scan_mode": "quick"}),
                                               encoding="utf-8")
    env = {**os.environ, "WTH_SKIP_SETUP": "1", "WTH_PLAIN": "1"}
    proc = subprocess.run(
        [sys.executable, str(REPO / "wth-ai-main.py"), "report", "--bundle", "out/bundle",
         "--format", "pdf"], cwd=tmp_path, capture_output=True, text=True,
        timeout=180, env=env)
    assert proc.returncode == 0, proc.stderr
    written = tmp_path / "out" / "report.pdf"
    assert written.is_file()
    assert b"deadbeef" in written.read_bytes()