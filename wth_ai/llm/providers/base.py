"""Base LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class BaseLLMProvider(ABC):
    provider_type: str = "base"
    # Capability defaults; subclasses override as appropriate.
    tool_calling: bool = False

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def list_models(self) -> list[str]:
        ...

    # --- Unified capability/inspection interface (Fase 2) ---

    @property
    def name(self) -> str:
        """Human-facing provider name (subclasses with sub-types override)."""
        return self.provider_type

    def supports_tool_calling(self) -> bool:
        return self.tool_calling

    def get_context_length(self, model: str | None = None) -> int | None:
        """Best-effort context window; None when unknown."""
        return None

    def get_rate_limit_info(self) -> dict[str, Any] | None:
        """Provider rate-limit metadata when exposed; None otherwise."""
        return None

    def normalize_error(self, exc: Exception) -> dict[str, Any]:
        """Map a provider exception into a uniform error dict."""
        kind = "unknown"
        msg = str(exc)
        low = msg.lower()
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 401 or "unauthorized" in low or "api key" in low or "api_key" in low:
            kind = "auth"
        elif status == 429 or "rate limit" in low or "too many requests" in low:
            kind = "rate_limit"
        elif status == 404 or "not found" in low or "no such model" in low:
            kind = "not_found"
        elif "timeout" in low or "timed out" in low:
            kind = "timeout"
        elif "connection" in low or "resolve" in low or "unreachable" in low:
            kind = "connection"
        return {"type": kind, "message": msg, "status": status}

    def test_connection(self, model: str | None = None) -> dict[str, Any]:
        """Lightweight reachability check (no completion)."""
        try:
            if not self.available():
                return {"ok": False, "error": {"type": "auth", "message": "provider not available / key missing"}}
            self.list_models()
            return {"ok": True, "error": None}
        except Exception as exc:  # noqa: BLE001 - normalized below
            return {"ok": False, "error": self.normalize_error(exc)}

    def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        role: str | None = None,
        timeout: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run a completion and return a normalized envelope (never raises)."""
        envelope: dict[str, Any] = {
            "provider": self.name,
            "model": model,
            "role": role,
            "status": "error",
            "content": "",
            "usage": {},
            "tool_calls": [],
            "error": None,
        }
        try:
            content = self.chat(model, messages, timeout=timeout, max_tokens=max_tokens)
            envelope["status"] = "success"
            envelope["content"] = content
        except Exception as exc:  # noqa: BLE001 - normalized into envelope
            envelope["error"] = self.normalize_error(exc)
        return envelope

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
