"""Phase 7: secret exposure detection with redacted output."""

import re

from . import ReconPhase


class SecretDetectionPhase(ReconPhase):
    """Detect common secret assignments in supplied text and redact values."""

    phase, name = 7, "secret_detection"
    pattern = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?([^\s\"']{8,})")

    async def run(self, context: dict) -> dict:
        result = await super().run(context)
        findings = []
        for source in context.get("text_sources", []) + context.get("javascript_sources", []):
            findings.extend({"type": match[0].lower(), "value": "***"} for match in self.pattern.findall(source))
        result["observations"] = findings
        return result
