"""Unified report builder (Fase 6, flowchart Stage 9).

Aggregates a job's artifacts (recon summary, asset graph, risk heatmap,
priority targets, triage findings, evidence bundles, hypotheses, audit log)
into a single report and exports JSON / Markdown / HTML (PDF optional).
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class ReportBuilder:
    def __init__(self, job_dir: Path, *, target: str = "") -> None:
        self.job_dir = Path(job_dir)
        self.norm_dir = self.job_dir / "normalized"
        self.target = target

    # ----------------------------------------------------------------- #
    def gather(self) -> dict[str, Any]:
        recon_summary = _load(self.job_dir / "recon_summary.json") or {}
        graph = _load(self.norm_dir / "asset_graph.json") or {}
        heatmap = _load(self.norm_dir / "risk_heatmap.json") or {}
        priority = _load(self.job_dir / "priority_targets.json") or []
        triage = _load(self.job_dir / "triage_findings.json") or {}
        hypotheses = _load(self.job_dir / "hypotheses.json") or []
        manifest = _load(self.job_dir / "exec_manifest.json") or {}
        target = self.target or recon_summary.get("target") or manifest.get("target") or "target"
        findings = triage.get("findings", []) if isinstance(triage, dict) else []
        sev_counts = triage.get("severity_counts", {}) if isinstance(triage, dict) else {}
        return {
            "target": target,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "recon_summary": recon_summary,
            "asset_inventory": recon_summary.get("assets", {}),
            "asset_graph": {"node_count": graph.get("node_count", 0),
                            "edge_count": graph.get("edge_count", 0),
                            "node_types": graph.get("node_types", {}),
                            "clusters": graph.get("clusters", [])[:10]},
            "risk_heatmap": heatmap,
            "priority_targets": priority[:20],
            "findings": findings,
            "severity_counts": sev_counts,
            "hypotheses": hypotheses[:20],
            "execution": {"task_count": manifest.get("task_count", 0),
                          "mode": manifest.get("mode", "")},
        }

    # ----------------------------------------------------------------- #
    def to_markdown(self, r: dict[str, Any]) -> str:
        sev = r["severity_counts"]
        L: list[str] = [
            f"# Security Assessment Report — {r['target']}",
            "",
            f"_Generated {r['generated_at']} · Authorized, evidence-driven, non-destructive._",
            "",
            "## 1. Executive Summary",
            self._exec_summary(r),
            "",
            "## 2. Scope & Methodology",
            "- Authorized testing only; recon-only by default, active testing gated by execution mode.",
            "- Evidence-driven (TTST): every finding is a hypothesis until human-verified.",
            f"- Execution mode: `{r['execution'].get('mode') or 'recon'}`; "
            f"planned test tasks: {r['execution'].get('task_count', 0)}.",
            "",
            "## 3. Attack Surface Inventory",
        ]
        for k, v in (r["asset_inventory"] or {}).items():
            L.append(f"- {k}: {v}")
        g = r["asset_graph"]
        L += [
            "",
            "## 4. Asset Graph Summary",
            f"- Nodes: {g['node_count']} · Edges: {g['edge_count']}",
            f"- Node types: {json.dumps(g['node_types'])}",
            "",
            "## 5. Risk Heatmap",
        ]
        for tier in ("critical", "high", "medium", "low", "info"):
            items = (r["risk_heatmap"] or {}).get(tier, [])
            L.append(f"- {tier.upper()}: {len(items)}")
        L += ["", "## 6. Prioritized Targets"]
        if r["priority_targets"]:
            for t in r["priority_targets"][:15]:
                L.append(f"- **{t.get('asset')}** (score {t.get('score')}, "
                         f"{t.get('severity', 'info')}) — {', '.join(t.get('reasons', [])[:4])}")
        else:
            L.append("- (none)")

        L += ["", "## 7. Technical Findings"]
        if r["findings"]:
            for f in r["findings"]:
                L += [
                    f"### [{f.get('severity', 'info').upper()}] {f.get('title')}",
                    f"- Asset: `{f.get('asset')}`",
                    f"- Category: {f.get('category')} · Confidence: {f.get('confidence')} · "
                    f"Proven: {f.get('proven')}",
                    f"- Detail: {f.get('detail')}",
                    f"- Remediation: {f.get('fix_recommendation')}",
                    f"- Retest required: {f.get('retest_required')}",
                    f"- Evidence bundle: `{f.get('evidence_bundle', {}).get('bundle_id', '-')}` "
                    f"(sha256 `{f.get('evidence_bundle', {}).get('artifact_sha256', '')[:16]}…`)",
                    "",
                ]
        else:
            L.append("- No active findings (recon-only or none surfaced).")

        if r["hypotheses"]:
            L += ["", "## 8. AI Hypotheses (unproven)"]
            for h in r["hypotheses"][:10]:
                L.append(f"- {h.get('title')} — `{h.get('asset')}` "
                         f"(confidence {h.get('confidence')})")

        L += [
            "",
            "## 9. Remediation Summary",
            self._remediation_summary(r),
            "",
            "## 10. Retest Checklist",
        ]
        retests = [f for f in r["findings"] if f.get("retest_required")]
        if retests:
            for f in retests:
                L.append(f"- [ ] {f.get('title')} on `{f.get('asset')}`")
        else:
            L.append("- [ ] (no items flagged for retest)")
        L += ["", "## 11. Appendix: Audit", f"- Severity counts: {json.dumps(sev)}"]
        return "\n".join(L) + "\n"

    def _exec_summary(self, r: dict[str, Any]) -> str:
        sev = r["severity_counts"]
        n = sum(sev.values()) if sev else 0
        inv = r["asset_inventory"] or {}
        high = sev.get("critical", 0) + sev.get("high", 0)
        return (
            f"Assessment of **{r['target']}** mapped "
            f"{inv.get('subdomains', 0)} subdomain(s), {inv.get('urls', 0)} URL(s) and "
            f"{inv.get('endpoints', 0)} endpoint(s). Triage produced {n} finding(s) "
            f"({high} high/critical). All findings are evidence-backed hypotheses pending "
            f"human verification; testing was authorized and non-destructive."
        )

    def _remediation_summary(self, r: dict[str, Any]) -> str:
        cats: dict[str, str] = {}
        for f in r["findings"]:
            cats.setdefault(f.get("category", "other"), f.get("fix_recommendation", ""))
        if not cats:
            return "- No remediation items."
        return "\n".join(f"- **{c}**: {fix}" for c, fix in cats.items())

    def to_html(self, r: dict[str, Any]) -> str:
        body = html.escape(self.to_markdown(r))
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>Report — {html.escape(r['target'])}</title>"
            "<style>body{background:#0b1020;color:#e5e7eb;font-family:ui-monospace,"
            "Menlo,Consolas,monospace;max-width:900px;margin:2rem auto;padding:0 1rem;"
            "line-height:1.5}h1,h2,h3{color:#22d3ee}a{color:#a855f7}"
            "pre{white-space:pre-wrap;border-left:3px solid #22d3ee;padding-left:1rem}</style>"
            f"</head><body><pre>{body}</pre></body></html>"
        )

    # ----------------------------------------------------------------- #
    def build(self, *, formats: tuple[str, ...] = ("json", "md", "html")) -> dict[str, str]:
        r = self.gather()
        written: dict[str, str] = {}
        if "json" in formats:
            p = self.job_dir / "report.json"
            p.write_text(json.dumps(r, indent=2), encoding="utf-8")
            written["json"] = str(p)
        md = self.to_markdown(r)
        if "md" in formats:
            p = self.job_dir / "report.md"
            p.write_text(md, encoding="utf-8")
            written["md"] = str(p)
        html_doc = self.to_html(r)
        if "html" in formats:
            p = self.job_dir / "report.html"
            p.write_text(html_doc, encoding="utf-8")
            written["html"] = str(p)
        if "pdf" in formats:
            pdf = self._try_pdf(html_doc)
            if pdf:
                written["pdf"] = pdf
        return written

    def _try_pdf(self, html_doc: str) -> str | None:
        """Optional PDF via weasyprint; gracefully skipped if unavailable."""
        try:
            from weasyprint import HTML  # type: ignore

            out = self.job_dir / "report.pdf"
            HTML(string=html_doc).write_pdf(str(out))
            return str(out)
        except Exception:  # noqa: BLE001
            return None


def build_report(job_dir: Path | str, *, target: str = "",
                 formats: tuple[str, ...] = ("json", "md", "html")) -> dict[str, str]:
    return ReportBuilder(Path(job_dir), target=target).build(formats=formats)
