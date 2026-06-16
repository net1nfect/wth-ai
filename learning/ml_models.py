"""Lightweight deterministic model definitions."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MLModel:
    """Online average reward model."""

    rewards: dict[str, list[float]] = field(default_factory=dict)

    def update(self, action: str, reward: float) -> None:
        """Add an observed reward."""
        self.rewards.setdefault(action, []).append(float(reward))

    def predict(self, action: str) -> float:
        """Return mean reward for an action."""
        values = self.rewards.get(action, [])
        return sum(values) / len(values) if values else 0.0

    def snapshot(self) -> dict[str, Any]:
        """Return model state."""
        return {"scores": {key: self.predict(key) for key in self.rewards}}
