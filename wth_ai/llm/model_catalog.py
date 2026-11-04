"""Static model capability catalog.

Providers expose model *ids* but rarely their limits, and the router needs a
context length to decide whether a plan fits. This is a small, honest table:
known models get exact numbers; unknown ids fall back to a family heuristic
that returns a conservative default rather than ``None`` -- a router that
cannot size a request cannot route it.

The catalog is data, not policy: adding a model here never changes which
provider is used.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Used when nothing better is known. Deliberately small so an oversized
#: prompt fails fast instead of being silently truncated by a provider.
DEFAULT_CONTEXT_LENGTH = 32_768


@dataclass(frozen=True)
class ModelCapability:
    model: str
    context_length: int | None = None
    max_output_tokens: int | None = None
    provider: str = ""
    supports_tools: bool = True
    supports_vision: bool = False


_EXACT: dict[str, ModelCapability] = {
    # Anthropic
    "claude-3-5-sonnet": ModelCapability("claude-3-5-sonnet", 200_000, 8_192, "anthropic"),
    "claude-3-5-haiku": ModelCapability("claude-3-5-haiku", 200_000, 8_192, "anthropic"),
    "claude-3-opus": ModelCapability("claude-3-opus", 200_000, 4_096, "anthropic"),
    "claude-sonnet-4": ModelCapability("claude-sonnet-4", 200_000, 16_384, "anthropic"),
    "claude-opus-4": ModelCapability("claude-opus-4", 200_000, 16_384, "anthropic"),
    # OpenAI
    "gpt-4o": ModelCapability("gpt-4o", 128_000, 16_384, "openai", True, True),
    "gpt-4o-mini": ModelCapability("gpt-4o-mini", 128_000, 16_384, "openai", True, True),
    "gpt-4.1": ModelCapability("gpt-4.1", 1_000_000, 32_768, "openai", True, True),
    "gpt-4.1-mini": ModelCapability("gpt-4.1-mini", 1_000_000, 32_768, "openai", True, True),
    "o3": ModelCapability("o3", 200_000, 100_000, "openai", True),
    "o4-mini": ModelCapability("o4-mini", 200_000, 100_000, "openai", True),
    # Google
    "gemini-2.5-pro": ModelCapability("gemini-2.5-pro", 1_048_576, 65_536, "google", True, True),
    "gemini-2.5-flash": ModelCapability("gemini-2.5-flash", 1_048_576, 65_536, "google", True, True),
    "gemini-2.0-flash": ModelCapability("gemini-2.0-flash", 1_048_576, 8_192, "google", True, True),
    # DeepSeek / Qwen / Llama families served by many gateways
    "deepseek-chat": ModelCapability("deepseek-chat", 128_000, 8_192, "deepseek"),
    "deepseek-reasoner": ModelCapability("deepseek-reasoner", 128_000, 8_192, "deepseek"),
    "qwen2.5-72b-instruct": ModelCapability("qwen2.5-72b-instruct", 131_072, 8_192, "qwen"),
    "llama-3.3-70b-instruct": ModelCapability("llama-3.3-70b-instruct", 128_000, 4_096, "meta"),
    # Local
    "llama3.1": ModelCapability("llama3.1", 131_072, 4_096, "ollama"),
    "qwen2.5": ModelCapability("qwen2.5", 32_768, 4_096, "ollama"),
}

#: Substring -> context length. Checked in order, first match wins.
_FAMILY_HINTS: tuple[tuple[str, int], ...] = (
    ("claude", 200_000),
    ("gemini", 1_048_576),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("gpt-4", 128_000),
    ("gpt-3.5", 16_385),
    ("o3", 200_000),
    ("o4", 200_000),
    ("deepseek", 128_000),
    ("qwen", 131_072),
    ("llama", 128_000),
    ("mistral", 32_768),
    ("mixtral", 32_768),
)


def _normalize(model: str) -> str:
    """Strip an ``org/`` prefix and any ``:free``/``:tag`` suffix."""
    name = (model or "").strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[1]
    if ":" in name:
        name = name.split(":", 1)[0]
    return name


def get(model: str | None) -> ModelCapability | None:
    """Look up a model, falling back to family heuristics.

    Returns ``None`` only for an empty id -- every other id gets a capability
    with a conservative context length, because "unknown" and "unsupported"
    are different answers and callers cannot act on ``None``.
    """
    if not model:
        return None
    name = _normalize(model)
    cap = _EXACT.get(name)
    if cap is not None:
        return cap
    for candidate, exact in _EXACT.items():
        if name.startswith(candidate):
            return ModelCapability(
                model,
                exact.context_length,
                exact.max_output_tokens,
                exact.provider,
                exact.supports_tools,
                exact.supports_vision,
            )
    for hint, length in _FAMILY_HINTS:
        if hint in name:
            return ModelCapability(model, length, min(length // 4, 16_384), hint)
    return ModelCapability(model, DEFAULT_CONTEXT_LENGTH, DEFAULT_CONTEXT_LENGTH // 4)


def context_length(model: str | None) -> int | None:
    cap = get(model)
    return cap.context_length if cap else None


def known_models() -> list[str]:
    return sorted(_EXACT)
