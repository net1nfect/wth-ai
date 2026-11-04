"""Laporan HTML statis dari bundel — tanpa server, tanpa JS eksternal.

`AGENTS.md` melarang dashboard; ini bukan dashboard: satu file yang dibuka
lewat `file://` dan bisa dilampirkan ke slide atau email.
"""

from __future__ import annotations

import html
import json
from pathlib import Path


def render_html(bundle: Path) -> str:
    root = Path(bundle)
    findings = json.loads((root / "findings.json").read_text(encoding="utf-8"))
    root_hash = ""
    root_file = root / "root_hash.txt"
    if root_file.is_file():
        root_hash = root_file.read_text(encoding="utf-8").strip()
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(f.get('id', '')))}</td>"
        f"<td>{html.escape(str(f.get('severity', '')))}</td>"
        f"<td>{html.escape(str(f.get('state', '')))}</td>"
        f"<td>{html.escape(str(f.get('hypothesis', '')))}</td>"
        "</tr>"
        for f in findings
    )
    return (
        "<!doctype html>\n"
        "<html lang=\"id\"><head><meta charset=\"utf-8\">"
        "<title>WTH-AI — laporan</title>\n"
        "<style>body{font-family:system-ui,sans-serif;background:#1a1b26;color:#c0caf5;"
        "padding:2rem}table{border-collapse:collapse;width:100%}"
        "th,td{border-bottom:1px solid #2a2e3f;padding:.4rem;text-align:left}"
        "code{color:#7aa2f7}</style></head><body>\n"
        f"<h1>WTH-AI — {len(findings)} temuan</h1>\n"
        f"<p>root hash: <code>{html.escape(root_hash)}</code></p>\n"
        "<table><thead><tr><th>id</th><th>severity</th><th>state</th>"
        f"<th>hypothesis</th></tr></thead><tbody>\n{rows}\n</tbody></table>\n"
        "</body></html>\n"
    )


def write_html(bundle: Path, out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(bundle), encoding="utf-8")
    return out