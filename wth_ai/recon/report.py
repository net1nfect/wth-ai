"""Recon report writer: recon_report.md + recon_summary.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wth_ai.recon.model import PriorityTarget, ReconPlan, ToolRun

if TYPE_CHECKING:
    from wth_ai.recon.normalizer import ReconNormalizer


def build_summary(
    target: str,
    plan: ReconPlan,
    normalizer: "ReconNormalizer",
    priority: list[PriorityTarget],
    tool_runs: list[ToolRun],
    changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_status: dict[str, int] = {}
    for r in tool_runs:
        run_status[r.status] = run_status.get(r.status, 0) + 1
    return {
        "target": target,
        "profile": plan.profile,
        "target_type": plan.target_type,
        "planned_tools": len(plan.selected),
        "skipped_tools": len(plan.skipped),
        "tool_runs": run_status,
        "assets": normalizer.summary(),
        "priority_targets": len(priority),
        "top_priority": [t.to_dict() for t in priority[:10]],
        "changes": changes or {},
    }


def write_report(
    out_dir: Path,
    target: str,
    plan: ReconPlan,
    normalizer: "ReconNormalizer",
    priority: list[PriorityTarget],
    tool_runs: list[ToolRun],
    changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(target, plan, normalizer, priority, tool_runs, changes)

    (out_dir / "recon_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    s = normalizer.summary()
    lines: list[str] = [
        f"# Recon Report — {target}",
        "",
        "> Authorized, recon-only, non-destructive.",
        "",
        "## Overview",
        f"- Profile: `{plan.profile}` (target type: {plan.target_type})",
        f"- Tools planned: {len(plan.selected)} · skipped: {len(plan.skipped)}",
        f"- Tool runs: {summary['tool_runs']}",
        "",
        "## Asset Inventory",
        f"- Subdomains: {s['subdomains']}",
        f"- DNS records: {s['dns_records']}",
        f"- URLs: {s['urls']}  ·  Alive (HTTP probes): {s['http_probes']}",
        f"- Endpoints: {s['endpoints']}  ·  Parameters: {s['params']}",
        f"- JS intel: {s['js_intel']}  ·  Secret candidates (masked): {s['secret_candidates']}",
        f"- TLS certs: {s['tls']}  ·  Fingerprints: {s['fingerprints']}",
        f"- Cloud candidates: {s['cloud_candidates']}",
        f"- Finding candidates: {s['findings']}",
        "",
        "## Top Priority Targets",
    ]
    if priority:
        for t in priority[:10]:
            lines.append(f"- **{t.asset}** (score {t.score}) — {', '.join(t.reasons[:5])}")
    else:
        lines.append("- (none scored)")

    if s["findings"]:
        lines += ["", "## Finding Candidates (passive)"]
        for f in list(normalizer.findings.values())[:25]:
            lines.append(f"- [{f.severity_hint}] {f.title} — {f.asset}")

    if changes and changes.get("changed"):
        lines += ["", "## Changes Since Last Run"]
        for key, val in changes.items():
            if isinstance(val, list) and val:
                lines.append(f"- {key}: {len(val)} ({', '.join(map(str, val[:5]))})")

    lines += [
        "",
        "## Tool Runs",
    ]
    for r in tool_runs:
        suffix = f" — {r.error}" if r.error else ""
        lines.append(f"- {r.tool}: {r.status} ({len(r.normalized_items)} items){suffix}")

    (out_dir / "recon_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
