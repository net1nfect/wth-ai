"""Compliance mapping."""

from typing import Any


RULES = {
    "credential_exposure": ["GDPR-32", "HIPAA-164.312", "PCI-DSS-8"],
    "weak_tls": ["GDPR-32", "HIPAA-164.312", "PCI-DSS-4"],
    "missing_access_control": ["GDPR-25", "HIPAA-164.312", "PCI-DSS-7"],
}


class ComplianceMapper:
    """Map finding types to high-level compliance controls."""

    def map(self, findings: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Return mappings keyed by finding identifier."""
        return {
            str(item.get("id", index)): RULES.get(str(item.get("type")), [])
            for index, item in enumerate(findings)
        }
