"""Reinforcement learning core."""

import json
from pathlib import Path
from typing import Any

from .ml_models import MLModel


class ReinforcementEngine:
    """Learn prioritization strategies from non-sensitive feedback."""

    def __init__(self) -> None:
        self.model = MLModel()

    def collect_metrics(self, samples: list[dict[str, Any]]) -> None:
        """Update action rewards."""
        for sample in samples:
            self.model.update(str(sample.get("action", "unknown")), float(sample.get("reward", 0.0)))

    def train(self) -> dict[str, Any]:
        """Return current learned scores."""
        return self.model.snapshot()

    def update_strategies(self) -> list[str]:
        """Return actions ordered by learned reward."""
        return sorted(self.model.rewards, key=self.model.predict, reverse=True)

    def persist(self, path: str | Path) -> Path:
        """Persist non-sensitive model scores."""
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.model.snapshot(), indent=2), encoding="utf-8")
        return output
