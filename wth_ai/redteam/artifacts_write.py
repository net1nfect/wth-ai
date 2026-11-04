"""Tulis artefak hasil scan: satu markdown per temuan + indeks mesin-baca."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from wth_ai.redteam.models import Finding


def write_findings(root: Path, findings: list[Finding]) -> dict[str, Path]:
    out = Path(root)
    vuln_dir = out / "vulnerabilities"
    vuln_dir.mkdir(parents=True, exist_ok=True)

    for finding in findings:
        body = [
            f"# {finding.id} — {finding.hypothesis}",
            "",
            f"- state: {finding.state.value}",
            f"- severity: {finding.severity}",
            f"- provenance: {finding.provenance}",
            f"- attempts: {finding.attempts}",
            "",
            "## Bukti",
            *([f"- {item}" for item in finding.evidence] or ["- (tidak ada)"]),
            "",
            "## Attestation",
            "```json",
            json.dumps(finding.attestation, indent=2, ensure_ascii=False),
            "```",
        ]
        (vuln_dir / f"{finding.id}.md").write_text("\n".join(body) + "\n",
                                                   encoding="utf-8")

    json_path = out / "vulnerabilities.json"
    json_path.write_text(
        json.dumps([f.to_dict() for f in findings], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    csv_path = out / "vulnerabilities.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "severity", "state", "provenance", "hypothesis"])
        for finding in findings:
            writer.writerow([finding.id, finding.severity, finding.state.value,
                             finding.provenance, finding.hypothesis])
    return {"json": json_path, "csv": csv_path}