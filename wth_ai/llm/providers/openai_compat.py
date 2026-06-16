"""OpenAI-compatible providers (OpenAI, Anthropic via proxy, Google Gemini)."""

from __future__ import annotations

import json
from typing import Any, Callable

import requests

from wth_ai.llm.errors import LLMError
from wth_ai.llm.providers.base import BaseLLMProvider

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
}


class OpenAICompatProvider(BaseLLMProvider):
    """OpenAI-compatible HTTP provider.

    Serves OpenAI, Anthropic (proxy), Google Gemini (OpenAI-compat endpoint),
    Groq, and any ``custom`` endpoint that speaks the OpenAI chat API.
    """

    provider_type = "openai_compat"
    tool_calling = True

    def __init__(
        self,
        *,
        provider_name: str,
        api_key: str | None,
        base_url: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        default_timeout: int = 120,
    ) -> None:
        self.provider_name = provider_name
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URLS.get(provider_name, DEFAULT_BASE_URLS["openai"])).rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.default_timeout = default_timeout

    @property
    def name(self) -> str:
        return self.provider_name

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.provider_name == "anthropic":
            h["anthropic-version"] = "2023-06-01"
        return h

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=30)
            r.raise_for_status()
            data = r.json()
            return [m["id"] for m in data.get("data", []) if m.get("id")]
        except Exception:
            # Fallback common models when list endpoint unavailable
            fallbacks = {
                "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini"],
                "anthropic": ["claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"],
                "google": ["gemini-1.5-pro", "gemini-1.5-flash"],
                "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
            }
            return fallbacks.get(self.provider_name, [])

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
        if not self.api_key:
            raise LLMError(f"{self.provider_name.upper()} API key not set")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        url = f"{self.base_url}/chat/completions"
        r = requests.post(url, headers=self._headers(), json=payload, timeout=timeout or self.default_timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

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
        if not self.api_key:
            raise LLMError(f"{self.provider_name.upper()} API key not set")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        r = requests.post(url, headers=self._headers(), json=payload, timeout=timeout or self.default_timeout)
        r.raise_for_status()
        data = r.json()
        return {"message": data["choices"][0]["message"], "model": model, "raw": data}
