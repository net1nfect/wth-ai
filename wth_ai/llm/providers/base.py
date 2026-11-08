"""Base LLM provider interface."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Callable

import requests

from wth_ai.llm.errors import LLMError
from wth_ai.redaction import redact_secrets


def api_error_detail(body: str, *, limit: int = 400) -> str:
    """Extract the ACTIONABLE part of a provider's HTTP error body.

    Providers put the useful information in the JSON body and nowhere else. A
    live OpenRouter 429, for example, returns:

        {"error": {"message": "Provider returned error", "code": 429,
         "metadata": {"raw": "xingchen-agi/xing4.0-29b-a4b:free is temporarily
         rate-limited upstream. Please retry shortly, or add your own provider
         key to accumulate your rate limits", "limit_source":
         "upstream_provider_shared_pool", "remedy_hint": "Retry shortly, add
         your own provider key, or route to another provider"}}}

    ...and a 400 body names the invalid model id. Throwing that away left the
    operator with a bare "429 Client Error: Too Many Requests for url: ..." and
    no indication of what to change. The returned text is redacted before it is
    embedded in a message that may be logged.
    """
    raw = (body or "").strip()
    if not raw:
        return ""
    detail = raw
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        err = parsed.get("error", parsed)
        if isinstance(err, dict):
            message = str(err.get("message") or "").strip()
            meta = err.get("metadata")
            extra = ""
            if isinstance(meta, dict):
                extra = str(meta.get("raw") or meta.get("remedy_hint") or "").strip()
            if message and extra:
                detail = f"{message} — {extra}"
            elif message or extra:
                detail = message or extra
        elif err:
            detail = str(err)
    detail = " ".join(detail.split())
    return redact_secrets(detail[:limit])


def http_error_message(provider_name: str, model: str, url: str, status: int, body: str) -> str:
    """Operator-facing HTTP error message that always keeps the provider's own
    explanation.

    The 401/404/429 branches keep their provider-specific hints, but they no
    longer REPLACE the body's detail with a generic sentence -- the 429 branch
    used to discard the body entirely, which is exactly where the remedy lives.
    """
    detail = api_error_detail(body)
    suffix = f" [{detail}]" if detail else ""

    if status == 401:
        hint = (
            f"{provider_name}: API key ditolak (HTTP 401). "
            f"Cek vault/env (`ROUTER9_API_KEY` atau `wth-ai doctor`)."
        )
        return hint + suffix
    if status == 404:
        if provider_name == "9router" and "qwen/" in model:
            hint = (
                f"9router: model {model!r} tidak ada di gateway (HTTP 404). "
                f"Set reporter/planner ke model 9router, mis. `ag/gemini-pro-agent` "
                f"via `/model set all ag/gemini-pro-agent`."
            )
        else:
            hint = (
                f"{provider_name}: model atau endpoint tidak ditemukan (HTTP 404) — "
                f"model={model!r}, url={url}. Jalankan `wth-ai model list`."
            )
        return hint + suffix
    if status == 429:
        return (
            f"{provider_name}: rate limit (HTTP 429) — model={model!r}. "
            f"Tunggu sebentar atau ganti model." + suffix
        )
    if detail:
        return f"{provider_name}: HTTP {status} — {detail}"
    return f"{provider_name}: HTTP {status} for {url}"


def raise_for_status(provider_name: str, model: str, url: str, response: requests.Response) -> None:
    """Raise `LLMError` carrying the provider's response body.

    Replaces bare `response.raise_for_status()` at provider call sites: the
    `requests.HTTPError` it raises drops the JSON body, so the message an
    operator sees never contains the fix.
    """
    if response.ok:
        return
    try:
        body = response.text
    except Exception:  # noqa: BLE001 - body is best-effort context
        body = ""
    raise LLMError(http_error_message(provider_name, model, url, response.status_code, body))


def normalize_provider_usage(payload: Any) -> dict[str, Any]:
    """Thin alias so providers import one name for usage normalisation."""
    from wth_ai.llm.usage import normalize_usage

    return normalize_usage(payload)


class BaseLLMProvider(ABC):
    #: Usage block of the MOST RECENT call, in the canonical shape. Read by the
    #: router right after a successful call -- providers return plain text, so
    #: this side channel is how token accounting reaches the ledger.
    last_usage: dict[str, Any] = {}

    def _record_usage(self, payload: Any) -> dict[str, Any]:
        """Store the usage block of this call (no-op for unusable payloads)."""
        from wth_ai.llm.usage import normalize_usage

        self.last_usage = normalize_usage(payload)
        return self.last_usage

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
        """Human-facing provider name (subclasses with sub-types override).

        Use THIS in operator-facing messages, never `provider_name` directly:
        only `OpenAICompatProvider` stores that attribute, so reading it on
        OpenRouter/Ollama raised AttributeError from inside the error path and
        replaced the real HTTP error with a confusing attribute error.
        """
        return self.provider_type

    def supports_tool_calling(self) -> bool:
        return self.tool_calling

    def get_context_length(self, model: str | None = None) -> int | None:
        """Best-effort context window; None when unknown."""
        return None

    def get_rate_limit_info(self) -> dict[str, Any] | None:
        """Provider rate-limit metadata when exposed; None otherwise."""
        return None

    def list_models_detailed(self) -> list[dict[str, Any]]:
        """Full model metadata (context_length, pricing, capabilities) when
        the provider's API exposes it beyond bare model ids -- WU-110
        (PRD-013, model_catalog.py). Default: empty (most providers here
        don't expose this; only OpenRouterProvider overrides it today)."""
        return []

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
            "usage": getattr(self, "last_usage", None) or {},
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
        except Exception as primary_err:  # noqa: BLE001 -- re-raised with context
            if primary == backup:
                raise
            try:
                return self.chat(
                    backup, messages, timeout=timeout, stream=stream,
                    on_token=on_token, response_format=response_format,
                ), backup
            except Exception as backup_err:  # noqa: BLE001
                # Report BOTH attempts. Raising only the backup's error hid the
                # primary's, so an operator saw one failing model and no clue
                # that the fallback had also been tried -- verified live: both
                # :free models were rate-limited and only the second was named.
                raise LLMError(
                    f"{self.name}: model utama {primary!r} dan backup {backup!r} gagal — "
                    f"primary: {primary_err} | backup: {backup_err}"
                ) from backup_err

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
