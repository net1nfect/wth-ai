"""Report Builder Agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wth_ai.agents import BaseLLMAgent, load_prompt
from wth_ai.config import WTHConfig
from wth_ai.findings_db import FindingsDB
from wth_ai.models import SessionState
from wth_ai.llm.router import LLMProviderRouter


class ReportBuilderAgent(BaseLLMAgent):
    """Generate submission-ready security assessment reports."""

    def __init__(self, config: WTHConfig, client: LLMProviderRouter) -> None:
        super().__init__(config, client)
        self.system_prompt = load_prompt(config, "report_system.txt")

    def build(self, state: SessionState, findings_db: FindingsDB, *, evidence_vault: Any = None) -> str:
        verified = [f for f in findings_db.list_findings() if f.state.value in {"verified", "candidate"}]
        data = {
            "scope": state.scope.to_dict(),
            "permission": state.permission_mode.value,
            "phase": state.current_phase.value,
            "findings": [f.to_dict() for f in verified],
            "hypotheses": [h.to_dict() for h in findings_db.list_hypotheses()],
            "methodology": state.metadata.get("tools_used", []),
        }
        poc_blocks = ""
        if evidence_vault:
            for f in verified:
                poc = evidence_vault.export_poc_block(f.id)
                if "No stored" not in poc:
                    poc_blocks += f"\n\n## {f.title}\n{poc}"
        prompt = f"Buat Security Assessment Report dari data ini:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
        if poc_blocks:
            prompt += f"\n\nInclude these PoC blocks in the report:\n{poc_blocks}"
        if self.client.available():
            raw, _ = self._call(
                self.config.models.report,
                self.config.models.executor_backup,
                self.system_prompt,
                [],
                prompt,
                role="report",
            )
            return raw
        import os
        if os.environ.get("WTH_TEST_MODE") == "1":
            return self._offline_report(state, findings_db)
        from wth_ai.llm.errors import LLMUnavailableError
        raise LLMUnavailableError("LLM tidak tersedia untuk report. Jalankan `wth-ai setup`.")

    def save(self, content: str, state: SessionState) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_dir = Path(self.config.results_dir) / state.session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"report_{ts}.md"
        path.write_text(content, encoding="utf-8")
        json_path = out_dir / f"report_{ts}.json"
        json_path.write_text(
            json.dumps({"session_id": state.session_id, "target": state.scope.target, "content": content[:50000]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _offline_report(self, state: SessionState, findings_db: FindingsDB) -> str:
        verified = [f for f in findings_db.list_findings() if f.state.value in {"verified", "candidate"}]
        lines = [
            "# Bug Bounty Report — WTH-AI v3",
            "",
            "## Summary",
            f"Target `{state.scope.target}` assessed under `{state.permission_mode.value}` permission.",
            "",
            "## Scope",
            f"- In scope: {', '.join(state.scope.in_scope)}",
            f"- Environment: {state.scope.environment}",
            f"- Authorized: {state.scope.authorized}",
            "",
        ]
        for i, f in enumerate(verified, 1):
            lines.extend([
                f"## Finding {i}: {f.title}",
                "",
                "### Severity",
                f"{f.severity.upper()} (CVSS pending manual scoring)",
                "",
                "### Description",
                f.description or f.title,
                "",
                "### Steps to Reproduce",
                "1. See evidence below",
                "",
                "### Impact",
                f.impact or "See component analysis",
                "",
                "### Evidence",
            ])
            for ev in f.evidence[:10]:
                lines.append(f"- {ev}")
            lines.extend([
                "",
                "### Remediation",
                f.remediation or "Apply vendor patch / fix authorization / validate input",
                "",
                "---",
                "",
            ])
        if not verified:
            lines.append("_No verified findings yet. Run `/hunt` or `/plan` + `/execute` + `/verify`._")
        lines.extend([
            "",
            "## Methodology",
            "- WTH-AI v3: recon (10-phase) → plan → execute (module sandbox) → verify → report",
            "",
            "## Limitations",
            "- Set OPENROUTER_API_KEY for LLM-enhanced report narrative.",
        ])
        return "\n".join(lines)
