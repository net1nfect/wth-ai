"""Verifier Agent — evidence validation and triage."""

from __future__ import annotations

import re
from typing import Any

from wth_ai.agents import BaseLLMAgent, load_prompt
from wth_ai.agents.grounding import GroundingFirewall
from wth_ai.config import WTHConfig
from wth_ai.models import Finding, FindingState, SessionState
from wth_ai.llm.router import LLMProviderRouter


class VerifierAgent(BaseLLMAgent):
    """Validate candidate findings against 7-question gate."""

    def __init__(self, config: WTHConfig, client: LLMProviderRouter) -> None:
        super().__init__(config, client)
        self.system_prompt = load_prompt(config, "verifier_system.txt")
        self.grounding = GroundingFirewall()

    def verify(self, finding: Finding, state: SessionState, *, evidence_vault: Any = None) -> dict[str, Any]:
        gr = self.grounding.validate_claims(
            finding.title + " " + " ".join(finding.evidence),
            evidence_ids=[finding.id] if evidence_vault and evidence_vault.count() else [],
        )
        if not gr.grounded and finding.state == FindingState.VERIFIED:
            return {
                "finding_id": finding.id,
                "verdict": "needs_more_evidence",
                "reason": f"Grounding firewall: {gr.violations}",
                "evidence_quality": "low",
                "missing_evidence": "TX or EV citation required",
                "severity_adjustment": "downgrade",
                "replan_hint": "Gather [TX:...] proof via /execute",
                "model": "grounding",
                "raw": "",
            }
        schema_gate = ""
        if evidence_vault and state.metadata.get("task_evidence_schemas"):
            for tid, schema in state.metadata["task_evidence_schemas"].items():
                if schema:
                    ok, missing = evidence_vault.validate_schema(tid, schema)
                    if not ok:
                        schema_gate += f"\nEvidence vault gate FAILED for {tid}: missing {missing}"
        task_evidence = state.metadata.get("task_evidence_schemas", {})
        schema_hint = ""
        for tid, schema in task_evidence.items():
            if schema:
                schema_hint += f"\nExpected evidence schema for {tid}: {schema}"
        prompt = (
            f"Verifikasi finding ini:\n"
            f"ID: {finding.id}\n"
            f"Title: {finding.title}\n"
            f"Severity: {finding.severity}\n"
            f"State: {finding.state}\n"
            f"Component: {finding.component}\n"
            f"Evidence: {finding.evidence}\n"
            f"Impact: {finding.impact}\n"
            f"Scope: {state.scope.to_dict()}\n"
            f"Permission: {state.permission_mode.value}\n"
            f"{schema_hint}\n"
            f"{schema_gate}\n"
            f"Validate against evidence_schema if present. Reject if proof type mismatch.\n"
        )
        raw, model = self._call(
            self.config.models.verifier,
            self.config.models.planner_backup,
            self.system_prompt,
            [],
            prompt,
            role="verifier",
        )
        return self._parse_verification(raw, finding.id, model)

    def verify_evidence(
        self,
        finding: Finding,
        pairs: list[Any],
        *,
        vuln_class: str | None = None,
        expected: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """Verdict deterministik dari pasangan baseline/probe (GAP-010).

        Return None jika bukti kurang dari dua observasi -- pemanggil harus
        jatuh ke LLM atau menandai needs_more_evidence, bukan mengklaim verified.
        """
        from wth_ai.verify.deterministic import verify_finding

        return verify_finding(finding, pairs, vuln_class=vuln_class, expected=expected)

    def verify_with_evidence(
        self,
        finding: Finding,
        state: SessionState,
        *,
        pairs: list[Any] | None = None,
        vuln_class: str | None = None,
        evidence_vault: Any = None,
    ) -> dict[str, Any]:
        """Gate deterministik lebih dulu; LLM hanya bila bukti belum memutuskan.

        `verified`/`false_positive` dari aturan konkret tidak boleh ditimpa LLM.
        `needs_more_evidence` (atau bukti <2 observasi) baru diteruskan ke LLM
        bila tersedia; jika tidak, jatuh ke verifier offline.
        """
        candidate_pairs = list(pairs or [])
        if not candidate_pairs and evidence_vault is not None:
            getter = getattr(evidence_vault, "pairs_for_finding", None)
            if callable(getter):
                key = str(getattr(finding, "task_id", "") or finding.id)
                candidate_pairs = getter(key)
        klass = vuln_class or str(getattr(finding, "vuln_class", "") or "") or None
        deterministic = self.verify_evidence(finding, candidate_pairs, vuln_class=klass)
        if deterministic is not None and deterministic.get("verdict") in {"verified", "false_positive"}:
            return deterministic
        client = getattr(self, "client", None)
        if client is not None and client.available():
            return self.verify(finding, state, evidence_vault=evidence_vault)
        return self.verify_offline(
            finding, evidence_vault, pairs=candidate_pairs, vuln_class=klass
        )

    def verify_offline(
        self,
        finding: Finding,
        evidence_vault: Any = None,
        *,
        pairs: list[Any] | None = None,
        vuln_class: str | None = None,
    ) -> dict[str, Any]:
        """Rule-based verification when LLM unavailable.

        Jika ada >=2 observasi (baseline + probe), verifikasi deterministik
        dijalankan lebih dulu. Tanpa sinyal konkret hasilnya
        `needs_more_evidence`, bukan `verified`.
        """
        candidate_pairs = list(pairs or [])
        if not candidate_pairs and evidence_vault is not None:
            getter = getattr(evidence_vault, "pairs_for_finding", None)
            if callable(getter):
                candidate_pairs = getter(finding.id)
        deterministic = self.verify_evidence(finding, candidate_pairs, vuln_class=vuln_class)
        if deterministic is not None:
            return deterministic

        has_evidence = bool(finding.evidence)
        if evidence_vault:
            has_evidence = has_evidence or evidence_vault.count() > 0
        verdict = "needs_more_evidence"
        if has_evidence and len(finding.evidence) >= 2:
            verdict = "candidate"
        if not has_evidence:
            verdict = "false_positive" if finding.state == FindingState.VERIFIED else "needs_more_evidence"
        return {
            "finding_id": finding.id,
            "verdict": verdict,
            "reason": "Offline rule-based check — butuh LLM atau evidence manual untuk verified",
            "evidence_quality": "moderate" if has_evidence else "none",
            "missing_evidence": [] if has_evidence else ["concrete reproducible proof"],
            "severity_adjustment": "none",
            "replan_hint": "Gather more reproducible evidence" if not has_evidence else "",
            "model": "offline",
            "raw": "",
        }

    def _parse_verification(self, raw: str, finding_id: str, model: str) -> dict[str, Any]:
        def field(name: str) -> str:
            m = re.search(rf"-\s*{name}:\s*(.+)", raw, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        return {
            "finding_id": field("finding id") or finding_id,
            "verdict": field("verdict") or "needs_more_evidence",
            "reason": field("reason") or raw[:500],
            "evidence_quality": field("evidence quality") or "unknown",
            "missing_evidence": field("missing evidence"),
            "severity_adjustment": field("severity adjustment") or "none",
            "adjusted_severity": field("adjusted severity"),
            "reproducibility": field("reproducibility") or "unconfirmed",
            "scope_compliant": field("scope compliant") or "unknown",
            "next_safe_step": field("next safe step") or field("next step"),
            "replan_hint": field("replan hint") or field("next safe step") or field("missing evidence") or "",
            "model": model,
            "raw": raw,
        }

    def format_verification(self, v: dict[str, Any]) -> str:
        return "\n".join([
            "VERIFICATION:",
            f"- Finding ID: {v.get('finding_id')}",
            f"- Verdict: {v.get('verdict')}",
            f"- Reason: {v.get('reason')}",
            f"- Evidence quality: {v.get('evidence_quality')}",
            f"- Missing evidence: {v.get('missing_evidence', 'none')}",
            f"- Severity adjustment: {v.get('severity_adjustment')}",
            f"- Next safe step: {v.get('next_safe_step')}",
        ])
