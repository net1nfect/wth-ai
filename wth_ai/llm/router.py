"""LLM provider router — routes planner/executor/verifier to configured providers."""

from __future__ import annotations

import os
from typing import Any, Callable

from wth_ai.config import ProviderRoleConfig, WTHConfig
from wth_ai.llm.errors import LLMError, LLMUnavailableError
from wth_ai.llm.providers.base import BaseLLMProvider
from wth_ai.llm.providers.ollama import OllamaProvider
from wth_ai.llm.providers.openai_compat import OpenAICompatProvider
from wth_ai.llm.providers.openrouter import OpenRouterProvider


class LLMProviderRouter:
    """Unified LLM access per agent role with realtime enforcement."""

    def __init__(self, config: WTHConfig) -> None:
        self.config = config
        self._providers: dict[str, BaseLLMProvider] = {}
        self._role_providers: dict[str, BaseLLMProvider] = {}

    def _get_or_create_provider(self, ptype: str) -> BaseLLMProvider:
        if ptype in self._providers:
            return self._providers[ptype]
        secrets = self.config.secrets
        if ptype == "openrouter":
            p = OpenRouterProvider(
                api_key=secrets.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY"),
                base_url=self.config.openrouter.base_url,
                site_url=self.config.openrouter.site_url,
                app_name=self.config.openrouter.app_name,
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
                streaming=self.config.models.streaming,
                default_timeout=self.config.models.executor_timeout_seconds,
            )
        elif ptype == "ollama":
            p = OllamaProvider(
                base_url=secrets.ollama_base_url or "http://127.0.0.1:11434",
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
                default_timeout=self.config.models.executor_timeout_seconds,
            )
        elif ptype in {"openai", "anthropic", "google"}:
            key_map = {
                "openai": secrets.openai_api_key or os.environ.get("OPENAI_API_KEY"),
                "anthropic": secrets.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
                "google": secrets.google_api_key or os.environ.get("GOOGLE_API_KEY"),
            }
            p = OpenAICompatProvider(
                provider_name=ptype,
                api_key=key_map.get(ptype),
                base_url=getattr(secrets, f"{ptype}_base_url", None),
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
                default_timeout=self.config.models.executor_timeout_seconds,
            )
        else:
            p = OpenRouterProvider(
                api_key=secrets.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY"),
                base_url=self.config.openrouter.base_url,
                site_url=self.config.openrouter.site_url,
                app_name=self.config.openrouter.app_name,
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
                streaming=self.config.models.streaming,
                default_timeout=self.config.models.executor_timeout_seconds,
            )
        self._providers[ptype] = p
        return p

    def _role_config(self, role: str) -> ProviderRoleConfig:
        role_cfg = self.config.providers.get(role)
        if role_cfg:
            return role_cfg
        # Fallback to legacy models config
        model_map = {
            "planner": (self.config.models.planner, self.config.models.planner_backup),
            "executor": (self.config.models.executor, self.config.models.executor_backup),
            "verifier": (self.config.models.verifier, self.config.models.verifier),
            "report": (self.config.models.report, self.config.models.report),
        }
        primary, backup = model_map.get(role, (self.config.models.planner, self.config.models.planner_backup))
        return ProviderRoleConfig(type="openrouter", model=primary, backup_model=backup)

    def provider_for_role(self, role: str) -> BaseLLMProvider:
        if role not in self._role_providers:
            rc = self._role_config(role)
            self._role_providers[role] = self._get_or_create_provider(rc.type)
        return self._role_providers[role]

    def model_for_role(self, role: str) -> tuple[str, str]:
        rc = self._role_config(role)
        return rc.model, rc.backup_model or rc.model

    def available(self) -> bool:
        if os.environ.get("WTH_TEST_MODE") == "1":
            return True
        for role in ("planner", "executor", "verifier"):
            rc = self._role_config(role)
            if self._get_or_create_provider(rc.type).available():
                return True
        return bool(os.environ.get("OPENROUTER_API_KEY"))

    def require_available(self) -> None:
        if os.environ.get("WTH_TEST_MODE") == "1":
            return
        if not self.config.realtime_only:
            return
        if self.config.require_llm and not self.available():
            raise LLMUnavailableError(
                "LLM tidak tersedia. Jalankan `wth-ai setup` untuk konfigurasi API key dan model."
            )

    def chat_for_role(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        timeout: int | None = None,
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        self.require_available()
        provider = self.provider_for_role(role)
        primary, backup = self.model_for_role(role)
        return provider.chat_with_fallback(
            primary, backup, messages,
            timeout=timeout, stream=stream, on_token=on_token, response_format=response_format,
        )

    def chat_json_for_role(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        timeout: int | None = None,
    ) -> tuple[dict[str, Any], str]:
        self.require_available()
        provider = self.provider_for_role(role)
        primary, backup = self.model_for_role(role)
        if isinstance(provider, OpenRouterProvider):
            return provider.chat_json(primary, messages, backup=backup, timeout=timeout)
        raw, used = provider.chat_with_fallback(primary, backup, messages, timeout=timeout)
        import json
        import re
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw), used

    def chat_with_tools_for_role(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        self.require_available()
        provider = self.provider_for_role(role)
        primary, backup = self.model_for_role(role)
        return provider.chat_with_tools(primary, messages, tools=tools, backup=backup, timeout=timeout)

    def list_models(self, provider_type: str) -> list[str]:
        return self._get_or_create_provider(provider_type).list_models()

    def ping_provider(self, provider_type: str, model: str) -> bool:
        return self._get_or_create_provider(provider_type).ping(model)

    # Backward-compat shim for OpenRouterClient interface
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
        provider = self._get_or_create_provider("openrouter")
        return provider.chat_with_fallback(
            primary, backup, messages,
            timeout=timeout, stream=stream, on_token=on_token, response_format=response_format,
        )

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        backup: str | None = None,
        timeout: int | None = None,
    ) -> tuple[dict[str, Any], str]:
        provider = self._get_or_create_provider("openrouter")
        if isinstance(provider, OpenRouterProvider):
            return provider.chat_json(model, messages, backup=backup, timeout=timeout)
        raise LLMError("chat_json requires OpenRouter provider")

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
        provider = self._get_or_create_provider("openrouter")
        return provider.chat_with_tools(
            model, messages, tools=tools, backup=backup, timeout=timeout, tool_choice=tool_choice,
        )
