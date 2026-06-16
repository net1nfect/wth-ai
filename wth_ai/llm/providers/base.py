"""Base LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class BaseLLMProvider(ABC):
    provider_type: str = "base"

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        ...

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        ...

    def chat_with_fallback(
        self,
        primary: str,
        backup: str,
        messages: list[dict[str, Any]],
        *,
        timeout: int | None = None,
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        try:
            return self.chat(
                primary, messages, timeout=timeout, stream=stream,
                on_token=on_token, response_format=response_format,
            ), primary
        except Exception:
            if primary == backup:
                raise
            return self.chat(
                backup, messages, timeout=timeout, stream=stream,
                on_token=on_token, response_format=response_format,
            ), backup

    def chat_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        backup: str | None = None,
        timeout: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> dict[str, Any]:
        raise NotImplementedError(f"{self.provider_type} does not support tool calling")

    def ping(self, model: str) -> bool:
        """Validate provider with a minimal live request."""
        try:
            self.chat(model, [{"role": "user", "content": "ping"}], max_tokens=8, timeout=30)
            return True
        except Exception:
            return False
