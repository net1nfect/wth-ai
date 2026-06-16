"""LLM provider errors."""

from __future__ import annotations


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    """Raised when realtime mode requires LLM but no provider is configured."""
