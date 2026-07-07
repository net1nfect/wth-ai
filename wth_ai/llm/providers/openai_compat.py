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
    "9router": "http://127.0.0.1:20128/v1",
}


def _normalize_base_url(provider_name: str, base_url: str | None) -> str:
    raw = (base_url or DEFAULT_BASE_URLS.get(provider_name, DEFAULT_BASE_URLS["openai"])).rstrip("/")
    if provider_name == "9router" and not raw.endswith("/v1"):
        raw = f"{raw}/v1"
    return raw


def _http_error_message(provider_name: str, model: str, url: str, status: int, body: str) -> str:
    snippet = (body or "").strip()[:240]
    if status == 401:
        return (
            f"{provider_name}: API key ditolak (HTTP 401). "
            f"Cek vault/env (`ROUTER9_API_KEY` atau `wth doctor`)."
        )
    if status == 404:
        if provider_name == "9router" and "qwen/" in model:
            return (
                f"9router: model {model!r} tidak ada di gateway (HTTP 404). "
                f"Set reporter/planner ke model 9router, mis. `ag/gemini-pro-agent` via `/model set all ag/gemini-pro-agent`."
            )
        return (
            f"{provider_name}: model atau endpoint tidak ditemukan (HTTP 404) — model={model!r}, url={url}. "
            f"Jalankan `wth model list`."
        )
    if status == 429:
        return f"{provider_name}: rate limit (HTTP 429). Tunggu sebentar atau ganti model."
    if snippet:
        return f"{provider_name}: HTTP {status} — {snippet}"
    return f"{provider_name}: HTTP {status} for {url}"


def _raise_for_status(provider_name: str, model: str, url: str, response: requests.Response) -> None:
    if response.ok:
        return
    try:
        body = response.text
    except Exception:
        body = ""
    raise LLMError(_http_error_message(provider_name, model, url, response.status_code, body))


def _parse_response_json(text: str) -> dict[str, Any]:
    """Parse OpenAI JSON or SSE bodies (9router may stream even when stream=false)."""
    raw = (text or "").strip()
    if not raw:
        raise LLMError("empty LLM response body")
    if raw.startswith("data:") or "\ndata:" in raw:
        return _parse_sse_payload(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if "Extra data" in str(exc):
            obj, _ = json.JSONDecoder().raw_decode(raw)
            return obj
        raise


def _parse_sse_payload(text: str) -> dict[str, Any]:
    content_parts: list[str] = []
    last_obj: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        last_obj = obj
        choice = (obj.get("choices") or [{}])[0]
        msg = choice.get("message")
        if msg and msg.get("content"):
            return obj
        delta = choice.get("delta") or {}
        if delta.get("content"):
            content_parts.append(str(delta["content"]))
    if content_parts and last_obj is not None:
        merged = dict(last_obj)
        merged["choices"] = [{
            "index": 0,
            "message": {"role": "assistant", "content": "".join(content_parts)},
            "finish_reason": "stop",
        }]
        return merged
    if last_obj is not None:
        return last_obj
    raise LLMError("could not parse streaming LLM response")


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
        self.base_url = _normalize_base_url(provider_name, base_url)
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
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        url = f"{self.base_url}/chat/completions"
        r = requests.post(url, headers=self._headers(), json=payload, timeout=timeout or self.default_timeout)
        _raise_for_status(self.provider_name, model, url, r)
        data = _parse_response_json(r.text)
        return data["choices"][0]["message"]["content"]

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
            "stream": False,
        }
        url = f"{self.base_url}/chat/completions"
        r = requests.post(url, headers=self._headers(), json=payload, timeout=timeout or self.default_timeout)
        _raise_for_status(self.provider_name, model, url, r)
        data = _parse_response_json(r.text)
        return {"message": data["choices"][0]["message"], "model": model, "raw": data}
