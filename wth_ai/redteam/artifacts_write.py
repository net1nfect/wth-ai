"""Tulis artefak hasil scan: satu markdown per temuan + indeks mesin-baca."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from wth_ai.redaction import redact_data
from wth_ai.redteam.models import Finding


def write_findings(
    root: Path, findings: list[Finding], *, chains: list[Any] | None = None
) -> dict[str, Path]:
    out = Path(root)
    vuln_dir = out / "vulnerabilities"
    vuln_dir.mkdir(parents=True, exist_ok=True)

    # P1-0: satu chokepoint. Ketiga permukaan (md/json/csv) dirender dari dict
    # yang sudah diredaksi, jadi pemanggil baru tidak bisa lupa meredaksi.
    rows = [redact_data(f.to_dict()) for f in findings]
    for row in rows:
        body = [
            f"# {row['id']} — {row['hypothesis']}",
            "",
            f"- state: {row['state']}",
            f"- severity: {row['severity']}",
            f"- provenance: {row['provenance']}",
            f"- attempts: {row['attempts']}",
            "",
            "## Bukti",
            *([f"- {item}" for item in row["evidence"]] or ["- (tidak ada)"]),
            "",
            "## Attestation",
            "```json",
            json.dumps(row["attestation"], indent=2, ensure_ascii=False),
            "```",
        ]
        (vuln_dir / f"{row['id']}.md").write_text("\n".join(body) + "\n",
                                                  encoding="utf-8")

    json_path = out / "vulnerabilities.json"
    json_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    csv_path = out / "vulnerabilities.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "severity", "state", "provenance", "hypothesis"])
        for row in rows:
            writer.writerow([row["id"], row["severity"], row["state"],
                             row["provenance"], row["hypothesis"]])

    # B7: rantai temuan (konteks yang diwariskan antar-temuan) disimpan sebagai
    # artefak terpisah agar muncul di output mesin-baca, bukan hanya di memori.
    paths = {"json": json_path, "csv": csv_path}
    if chains:
        chain_json = []
        for chain in chains:
            d = chain.to_dict() if hasattr(chain, "to_dict") else chain
            chain_json.append(redact_data(d))
        chains_path = out / "chains.json"
        chains_path.write_text(
            json.dumps(chain_json, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        paths["chains"] = chains_path
    return paths