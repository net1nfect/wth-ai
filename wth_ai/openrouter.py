"""OpenRouter API client for dual-agent LLM calls."""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

import requests

from wth_ai.config import WTHConfig


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    """Thin wrapper around OpenRouter chat completions with streaming support."""

    def __init__(self, config: WTHConfig) -> None:
        self.config = config
        self.api_key = config.api_key()

    def available(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.config.openrouter.site_url,
            "X-Title": self.config.openrouter.app_name,
        }

    def _url(self) -> str:
        return f"{self.config.openrouter.base_url.rstrip('/')}/chat/completions"

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        if not self.api_key:
            raise OpenRouterError(
                f"OPENROUTER_API_KEY not set. Export {self.config.openrouter.api_key_env} to enable LLM agents."
            )

        use_stream = stream and self.config.models.streaming and on_token is not None
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.models.temperature,
            "max_tokens": max_tokens or self.config.models.max_tokens,
            "stream": use_stream,
        }
        if response_format:
            payload["response_format"] = response_format
        req_timeout = timeout or self.config.models.executor_timeout_seconds

        if use_stream:
            return self._chat_stream(payload, req_timeout, on_token)  # type: ignore[arg-type]

        try:
            response = requests.post(
                self._url(), headers=self._headers(), json=payload, timeout=req_timeout,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise OpenRouterError(
                f"Model lambat atau timeout ({req_timeout}s) — coba lagi atau ganti model di config/wth_ai_config.yaml"
            ) from exc
        except requests.RequestException as exc:
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(f"Unexpected OpenRouter response: {json.dumps(data)[:500]}") from exc

    def _chat_stream(
        self,
        payload: dict[str, Any],
        timeout: int,
        on_token: Callable[[str], None],
    ) -> str:
        full: list[str] = []
        try:
            with requests.post(
                self._url(),
                headers=self._headers(),
                json=payload,
                timeout=timeout,
                stream=True,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            full.append(token)
                            on_token(token)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except requests.RequestException as exc:
            raise OpenRouterError(f"OpenRouter stream failed: {exc}") from exc
        return "".join(full)

    def stream_tokens(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        timeout: int | None = None,
    ) -> Iterator[str]:
        """Yield tokens for REPL Live display."""
        collected: list[str] = []

        def on_token(t: str) -> None:
            collected.append(t)

        text = self.chat(model, messages, timeout=timeout, stream=True, on_token=on_token)
        if not collected and text:
            yield text
        else:
            yield from collected

    def chat_with_fallback(
        self,
        primary: str,
        backup: str,
        messages: list[dict[str, str]],
        *,
        timeout: int | None = None,
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Try primary model, fall back to backup on failure."""
        try:
            return self.chat(
                primary, messages, timeout=timeout, stream=stream, on_token=on_token,
                response_format=response_format,
            ), primary
        except OpenRouterError:
            if primary == backup:
                raise
            return self.chat(
                backup, messages, timeout=timeout, stream=stream, on_token=on_token,
                response_format=response_format,
            ), backup

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        backup: str | None = None,
        timeout: int | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Request JSON object response; falls back to parsing raw text."""
        fmt = {"type": "json_object"}
        try:
            raw, used = self.chat_with_fallback(
                model, backup or model, messages, timeout=timeout, response_format=fmt,
            )
        except OpenRouterError:
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
            raise OpenRouterError(f"Failed to parse JSON from model: {raw[:300]}")

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
        """Chat completion with tool calling; returns full message dict."""
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY not set")

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.config.models.temperature,
            "max_tokens": self.config.models.max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        req_timeout = timeout or self.config.models.executor_timeout_seconds

        for attempt_model in (model, backup or model):
            try:
                response = requests.post(
                    self._url(), headers=self._headers(), json={**payload, "model": attempt_model},
                    timeout=req_timeout,
                )
                response.raise_for_status()
                data = response.json()
                msg = data["choices"][0]["message"]
                return {"message": msg, "model": attempt_model, "raw": data}
            except (requests.RequestException, KeyError, IndexError, TypeError):
                if attempt_model == (backup or model):
                    raise OpenRouterError("chat_with_tools failed")
                continue
        raise OpenRouterError("chat_with_tools failed")
