"""JSON and HTML assessment report generation."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from core.exceptions import ReportingError

from .compliance_mapper import ComplianceMapper
from .visualization import Visualization


class ReportGenerator:
    """Generate evidence-focused reports with remediation guidance."""

    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = Path(output_dir or os.environ.get("WTH_REPORT_DIR", "reports"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.mapper = ComplianceMapper()
        self.visualization = Visualization()

    def compile(self, engagement: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
        """Compile normalized report data."""
        return {
            "engagement": engagement,
            "summary": {"total_findings": len(findings)},
            "findings": findings,
            "compliance": self.mapper.map(findings),
            "visualizations": {"severity": self.visualization.severity_chart(findings)},
            "remediation": [
                {"finding": item.get("id"), "recommendation": item.get("remediation", "Review and remediate the identified control gap.")}
                for item in findings
            ],
        }

    def generate(self, engagement_id: str, report: dict[str, Any], formats: list[str]) -> list[str]:
        """Generate requested JSON, HTML, and PDF report files."""
        outputs: list[str] = []
        for format_name in formats:
            if format_name == "json":
                path = self.output_dir / f"{engagement_id}.json"
                path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            elif format_name == "html":
                path = self.output_dir / f"{engagement_id}.html"
                body = html.escape(json.dumps(report, indent=2, default=str))
                path.write_text(f"<!doctype html><title>H1T4M-AI Report</title><pre>{body}</pre>", encoding="utf-8")
            elif format_name == "pdf":
                path = self.output_dir / f"{engagement_id}.pdf"
                try:
                    from reportlab.lib.pagesizes import A4
                    from reportlab.pdfgen import canvas
                except ImportError as exc:
                    raise ReportingError("PDF generation requires reportlab") from exc
                document = canvas.Canvas(str(path), pagesize=A4)
                document.setTitle("H1T4M-AI Assessment Report")
                document.drawString(40, 800, "H1T4M-AI Assessment Report")
                document.drawString(40, 780, f"Engagement: {engagement_id}")
                document.drawString(40, 760, f"Findings: {report['summary']['total_findings']}")
                document.save()
            else:
                raise ReportingError(f"Unsupported report format: {format_name}")
            outputs.append(str(path))
        return outputs
