"""Model capability registry (WU-110, PRD-013).

Two-tier resolution: SEED (hardcoded best-effort fallback, always available
even with no API key configured yet) -> LIVE CACHE (populated from
`OpenRouterProvider.list_models_detailed()` via `refresh_from_openrouter()`).
Live cache wins over seed for the same `model_id` once populated.

Honesty note on SEED figures: `anthropic/claude-3.5-sonnet`'s 200K context
window is a well-established, stable, publicly documented fact, used
directly to satisfy PRD-013's acceptance criterion
(`get_context_length("anthropic/claude-3.5-sonnet")` must return a real
number). The other entries (the actual default role models in
`config.py::ModelConfig` -- free-tier OpenRouter models) are conservative
best-effort estimates, NOT verified against a live API call at write-time --
`refresh_from_openrouter()` is the authoritative source once a real
OpenRouter API key is configured; seed is a fallback for when it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelCapability:
    context_length: int | None = None
    cost_per_1k_in: float | None = None
    cost_per_1k_out: float | None = None
    supports_tools: bool = False
    supports_json: bool = False


SEED: dict[str, ModelCapability] = {
    "anthropic/claude-3.5-sonnet": ModelCapability(
        context_length=200_000, cost_per_1k_in=0.003, cost_per_1k_out=0.015,
        supports_tools=True, supports_json=True,
    ),
    # Default role models (config.py::ModelConfig) -- free-tier OpenRouter;
    # figures below are conservative best-effort estimates, NOT live-verified.
    "nvidia/nemotron-3-super-120b-a12b:free": ModelCapability(
        context_length=128_000, cost_per_1k_in=0.0, cost_per_1k_out=0.0,
        supports_tools=True, supports_json=True,
    ),
    "qwen/qwen3-coder:free": ModelCapability(
        context_length=128_000, cost_per_1k_in=0.0, cost_per_1k_out=0.0,
        supports_tools=True, supports_json=True,
    ),
    "qwen/qwen3-coder-480b-a35b:free": ModelCapability(
        context_length=256_000, cost_per_1k_in=0.0, cost_per_1k_out=0.0,
        supports_tools=True, supports_json=True,
    ),
}

_live_cache: dict[str, ModelCapability] = {}


def get(model_id: str) -> ModelCapability | None:
    """Live cache wins over seed when both have an entry for `model_id`."""
    return _live_cache.get(model_id) or SEED.get(model_id)


def _to_cost_per_1k(raw: Any) -> float | None:
    """OpenRouter pricing is cost PER TOKEN as a numeric string (e.g.
    "0.000003") -- convert to cost per 1000 tokens."""
    if raw is None:
        return None
    try:
        return float(raw) * 1000
    except (TypeError, ValueError):
        return None


def refresh_from_openrouter(provider: Any) -> int:
    """Populate the live cache from `provider.list_models_detailed()`.
    Returns the number of models cached. Never raises -- best-effort, same
    except-Exception-return-empty convention as `list_models()` itself."""
    try:
        detailed = provider.list_models_detailed()
    except Exception:
        return 0
    count = 0
    for m in detailed:
        model_id = m.get("id")
        if not model_id:
            continue
        pricing = m.get("pricing") or {}
        supported = m.get("supported_parameters") or []
        _live_cache[model_id] = ModelCapability(
            context_length=m.get("context_length"),
            cost_per_1k_in=_to_cost_per_1k(pricing.get("prompt")),
            cost_per_1k_out=_to_cost_per_1k(pricing.get("completion")),
            supports_tools="tools" in supported,
            supports_json="response_format" in supported,
        )
        count += 1
    return count


def clear_live_cache() -> None:
    """Test/reset helper."""
    _live_cache.clear()
