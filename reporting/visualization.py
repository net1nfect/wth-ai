"""Portable visualization data generation."""

from collections import Counter
from typing import Any


class Visualization:
    """Generate chart-ready data without requiring a browser."""

    def severity_chart(self, findings: list[dict[str, Any]]) -> dict[str, list[Any]]:
        """Return severity counts."""
        counts = Counter(str(item.get("severity", "info")).lower() for item in findings)
        return {"labels": list(counts), "values": list(counts.values())}
