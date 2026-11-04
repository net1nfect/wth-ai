"""SARIF 2.1.0 minimal: cukup untuk GitHub code scanning.

Hanya temuan berstatus CONFIRMED yang masuk `results` — kandidat tidak boleh
tampil di code scanning seolah-olah sudah terbukti.
"""

from __future__ import annotations

from wth_ai.redteam.models import AttemptState, Finding

_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
LEVEL = {"critical": "error", "high": "error", "medium": "warning",
         "low": "note", "info": "note"}


def build_sarif(findings: list[Finding], *, tool_version: str = "1.0.0") -> dict:
    results = []
    for finding in findings:
        if finding.state is not AttemptState.CONFIRMED:
            continue
        results.append({
            "ruleId": f"WTH-{finding.vuln_class.upper()}",
            "level": LEVEL.get(finding.severity.lower(), "warning"),
            "message": {"text": finding.hypothesis},
            "properties": {"state": finding.state.value,
                           "provenance": finding.provenance},
        })
    return {
        "version": "2.1.0",
        "$schema": _SCHEMA,
        "runs": [{
            "tool": {"driver": {
                "name": "WTH-AI",
                "version": tool_version,
                "informationUri": "https://github.com/net1nfect/wth-ai",
            }},
            "results": results,
        }],
    }