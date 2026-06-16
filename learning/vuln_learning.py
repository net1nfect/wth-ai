"""Vulnerability validation learning."""


class VulnerabilityLearning:
    """Summarize safe validation outcomes."""

    def success_rate(self, outcomes: list[bool]) -> float:
        """Calculate validation success rate."""
        return sum(outcomes) / len(outcomes) if outcomes else 0.0
