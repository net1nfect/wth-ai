"""OpenRouter LLM provider."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import requests

from wth_ai.llm.errors import LLMError
from wth_ai.llm.providers.base import BaseLLMProvider


class OpenRouterProvider(BaseLLMProvider):
    provider_type = "openrouter"
    tool_calling = True

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://openrouter.ai/api/v1",
        site_url: str = "https://wth-ai.local",
        app_name: str = "WTH-AI",
        temperature: float = 0.3,
        max_tokens: int = 8192,
        streaming: bool = True,
        default_timeout: int = 120,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.site_url = site_url
        self.app_name = app_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.streaming = streaming
        self.default_timeout = default_timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.site_url,
            "X-Title": self.app_name,
        }

    def list_models(self) -> list[str]:
        if not self.api_key:
            return []
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=30)
            r.raise_for_status()
            data = r.json()
            return [m["id"] for m in data.get("data", []) if m.get("id")]
        except Exception:
            return []

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
            raise LLMError("OPENROUTER_API_KEY not set")
        use_stream = stream and self.streaming and on_token is not None
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": use_stream,
        }
        if response_format:
            payload["response_format"] = response_format
        req_timeout = timeout or self.default_timeout
        url = f"{self.base_url}/chat/completions"
        if use_stream:
            return self._chat_stream(url, payload, req_timeout, on_token)  # type: ignore[arg-type]
        r = requests.post(url, headers=self._headers(), json=payload, timeout=req_timeout)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]

    def _chat_stream(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: int,
        on_token: Callable[[str], None],
    ) -> str:
        full: list[str] = []
        with requests.post(url, headers=self._headers(), json=payload, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    token = chunk["choices"][0].get("delta", {}).get("content", "")
                    if token:
                        full.append(token)
                        on_token(token)
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
        return "".join(full)

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        backup: str | None = None,
        timeout: int | None = None,
    ) -> tuple[dict[str, Any], str]:
        fmt = {"type": "json_object"}
        try:
            raw, used = self.chat_with_fallback(
                model, backup or model, messages, timeout=timeout, response_format=fmt,
            )
        except LLMError:
            raw, used = self.chat_with_fallback(model, backup or model, messages, timeout=timeout)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            return json.loads(raw), used
        except json.JSONDecodeError:
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                return json.loads(m.group(0)), used
            raise LLMError(f"Failed to parse JSON: {raw[:300]}")

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
            raise LLMError("OPENROUTER_API_KEY not set")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        req_timeout = timeout or self.default_timeout
        url = f"{self.base_url}/chat/completions"
        for attempt_model in (model, backup or model):
            try:
                r = requests.post(
                    url, headers=self._headers(), json={**payload, "model": attempt_model},
                    timeout=req_timeout,
                )
                r.raise_for_status()
                data = r.json()
                return {"message": data["choices"][0]["message"], "model": attempt_model, "raw": data}
            except Exception:
                if attempt_model == (backup or model):
                    raise LLMError("chat_with_tools failed")
        raise LLMError("chat_with_tools failed")
