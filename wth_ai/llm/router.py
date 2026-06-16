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
        elif ptype in {"openai", "anthropic", "google", "groq", "custom"}:
            key_map = {
                "openai": secrets.openai_api_key or os.environ.get("OPENAI_API_KEY"),
                "anthropic": secrets.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
                "google": secrets.google_api_key or os.environ.get("GOOGLE_API_KEY"),
                "groq": secrets.groq_api_key or os.environ.get("GROQ_API_KEY"),
                "custom": secrets.custom_api_key or os.environ.get("CUSTOM_API_KEY"),
            }
            base_map = {
                "openai": secrets.openai_base_url,
                "anthropic": secrets.anthropic_base_url,
                "google": secrets.google_base_url,
                "groq": None,
                "custom": secrets.custom_base_url,
            }
            p = OpenAICompatProvider(
                provider_name=ptype,
                api_key=key_map.get(ptype),
                base_url=base_map.get(ptype),
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
        # Fallback to legacy models config (covers all Fase 2 roles).
        m = self.config.models
        model_map = {
            "planner": (m.planner, m.planner_backup),
            "recon": (m.recon, m.planner_backup),
            "executor": (m.executor, m.executor_backup),
            "code": (m.code, m.executor_backup),
            "verifier": (m.verifier, m.verifier),
            "reporter": (m.reporter, m.report),
            "report": (m.report, m.report),  # legacy alias
            "fallback": (m.fallback, m.fallback),
        }
        primary, backup = model_map.get(role, (m.planner, m.planner_backup))
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

    # --- Fase 2: unified role/provider operations ---

    def roles(self) -> tuple[str, ...]:
        from wth_ai.config import ROLE_NAMES
        return ROLE_NAMES

    def role_config(self, role: str) -> ProviderRoleConfig:
        return self._role_config(role)

    def set_role(
        self,
        role: str,
        provider: str,
        model: str,
        *,
        backup_model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Update the per-role provider+model mapping in config (not persisted)."""
        self.config.providers[role] = ProviderRoleConfig(
            type=provider, model=model, backup_model=backup_model, base_url=base_url,
        )
        self._role_providers.pop(role, None)

    def provider_key(self, provider_type: str) -> str | None:
        if provider_type == "ollama":
            return "local"  # local provider, no key required
        s = self.config.secrets
        from_config = {
            "openrouter": s.openrouter_api_key,
            "openai": s.openai_api_key,
            "anthropic": s.anthropic_api_key,
            "google": s.google_api_key,
            "groq": s.groq_api_key,
            "custom": s.custom_api_key,
        }.get(provider_type)
        if from_config:
            return from_config
        # Fall back to the vault directly (covers secrets not yet referenced
        # in user.yaml, e.g. right after `vault set` in the same process).
        vname = {
            "openrouter": "openrouter_api_key",
            "openai": "openai_api_key",
            "anthropic": "anthropic_api_key",
            "google": "google_api_key",
            "groq": "groq_api_key",
            "custom": "custom_api_key",
        }.get(provider_type)
        if not vname:
            return None
        from wth_ai.vault import SecretVault
        return SecretVault().get(vname)

    def provider_capabilities(self, provider_type: str) -> dict[str, Any]:
        p = self._get_or_create_provider(provider_type)
        return {
            "provider": provider_type,
            "tool_calling": p.supports_tool_calling(),
            "context_length": p.get_context_length(),
            "rate_limit": p.get_rate_limit_info(),
            "available": p.available(),
        }

    def test_provider(self, provider_type: str, model: str | None = None) -> dict[str, Any]:
        """Reachability + optional short completion, normalized."""
        p = self._get_or_create_provider(provider_type)
        conn = p.test_connection(model)
        result: dict[str, Any] = {
            "provider": provider_type,
            "reachable": conn["ok"],
            "error": conn["error"],
            "models_listed": 0,
            "completion_ok": False,
        }
        if not conn["ok"]:
            return result
        try:
            models = p.list_models()
            result["models_listed"] = len(models)
        except Exception as exc:  # noqa: BLE001
            result["error"] = p.normalize_error(exc)
        if model:
            env = p.complete(model, [{"role": "user", "content": "ping"}], timeout=30, max_tokens=8)
            result["completion_ok"] = env["status"] == "success"
            if env["status"] != "success":
                result["error"] = env["error"]
        return result

    def complete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        *,
        timeout: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Run a role completion returning the unified envelope (never raises)."""
        provider = self.provider_for_role(role)
        primary, _backup = self.model_for_role(role)
        return provider.complete(primary, messages, role=role, timeout=timeout, max_tokens=max_tokens)

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
