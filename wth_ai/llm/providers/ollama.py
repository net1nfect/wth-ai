"""Ollama local LLM provider."""

from __future__ import annotations

import json
from typing import Any, Callable

import requests

from wth_ai.llm.errors import LLMError
from wth_ai.llm.providers.base import BaseLLMProvider, raise_for_status


class OllamaProvider(BaseLLMProvider):
    provider_type = "ollama"
    tool_calling = True

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        temperature: float = 0.3,
        max_tokens: int = 8192,
        default_timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.default_timeout = default_timeout

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            url = f"{self.base_url}/api/tags"
            r = requests.get(url, timeout=10)
            raise_for_status(self.name, "list-models", url, r)
            models = r.json().get("models", [])
            return [m.get("name", "") for m in models if m.get("name")]
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
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": bool(stream and on_token),
            "options": {
                "temperature": temperature if temperature is not None else self.temperature,
                "num_predict": max_tokens or self.max_tokens,
            },
        }
        if response_format and response_format.get("type") == "json_object":
            payload["format"] = "json"
        url = f"{self.base_url}/api/chat"
        req_timeout = timeout or self.default_timeout
        if payload["stream"]:
            full: list[str] = []
            with requests.post(url, json=payload, timeout=req_timeout, stream=True) as r:
                raise_for_status(self.name, model, url, r)
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full.append(token)
                        on_token(token)  # type: ignore[misc]
                    if chunk.get("done"):
                        break
            return "".join(full)
        r = requests.post(url, json=payload, timeout=req_timeout)
        raise_for_status(self.name, model, url, r)
        data = r.json()
        self._record_usage(data)
        return data["message"]["content"]

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
        """Ollama tool calling via /api/chat tools field."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }
        url = f"{self.base_url}/api/chat"
        req_timeout = timeout or self.default_timeout
        r = requests.post(url, json=payload, timeout=req_timeout)
        raise_for_status(self.name, model, url, r)
        data = r.json()
        return {"message": data.get("message", {}), "model": model, "raw": data}
