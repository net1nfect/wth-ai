"""LLM provider router — routes planner/executor/verifier to configured providers."""

from __future__ import annotations

import os
from typing import Any, Callable

from wth_ai.config import ProviderRoleConfig, WTHConfig
from wth_ai.llm.errors import LLMError, LLMUnavailableError
from wth_ai.llm.model_io import ModelRequest, ModelResponse
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
        # Per-provider outcome of the most recent failover sweep; surfaced so a
        # caller (or an operator reading the error) can see WHY each provider
        # was skipped or failed instead of only the last one's error.
        self.last_failures: list[str] = []
        #: Set when the token budget has been reached in `warn` mode, so a caller
        #: can surface it next to the answer.
        self.budget_notice: str = ""
        #: `finish_reason` dari panggilan chat_for_role terakhir ("length" =
        #: respons terpotong di max_tokens). ChatAgent memakainya untuk
        #: memberi tahu operator, bukan diam-diam mengirim teks setengah jadi.
        self.last_finish_reason: str = ""

    def reset_provider_cache(self) -> None:
        """Lepas instance provider yang di-cache.

        Instance dibangun dengan `max_tokens`/timeout DARI CONFIG SAAT ITU --
        `/effort` mengubah config setelah provider mungkin sudah di-cache, jadi
        tanpa reset nilai baru tidak pernah sampai ke provider.
        """
        self._providers.clear()
        self._role_providers.clear()

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
        elif ptype == "9router":
            p = OpenAICompatProvider(
                provider_name="9router",
                api_key=secrets.router9_api_key or os.environ.get("ROUTER9_API_KEY") or os.environ.get("9ROUTER_API_KEY"),
                base_url=secrets.router9_base_url or self.config.nine_router.base_url,
                temperature=self.config.models.temperature,
                max_tokens=self.config.models.max_tokens,
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

    def _budget_gate(self, role: str) -> None:
        """Enforce the token budget BEFORE spending, not after.

        `WTH_TOKEN_BUDGET` used to be reported and never enforced: the agent
        could blow past it indefinitely, and the only place it appeared was the
        summary you read afterwards. Modes: `warn` (default — one notice per run,
        then continue), `stop` (refuse the call with an actionable message),
        `off`.
        """
        try:
            from wth_ai.llm.usage import BudgetExceeded, get_ledger

            allowed, message = get_ledger().check_budget()
        except Exception:  # noqa: BLE001 - accounting must never block a call
            return
        if allowed:
            if message:
                ledger = get_ledger()
                self.budget_notice = message
                if not getattr(ledger, "_warned", False):
                    ledger._warned = True
                    # Printed, not just stored: a warning the operator never sees
                    # is the same as no warning, and this is an operator tool.
                    import sys

                    print(f"[budget] {message}", file=sys.stderr)
            return
        raise BudgetExceeded(f"[role={role}] {message}")

    def _record_usage(self, role: str, provider_type: str, model: str, provider: Any) -> None:
        """One ledger entry per answered call. Never fatal.

        Providers return plain text, so their usage rides back on
        `provider.last_usage`. A provider that reports nothing still records a
        CALL with zero tokens: "not measured" is a fact the operator needs, and
        omitting the call would understate spend.
        """
        try:
            from wth_ai.llm.usage import get_ledger

            get_ledger().record(
                role=role,
                provider=provider_type,
                model=model,
                usage=getattr(provider, "last_usage", None),
            )
        except Exception:  # noqa: BLE001 - accounting must never break a call
            pass

    def _role_config(self, role: str) -> ProviderRoleConfig:
        if role == "supervisor":
            role = "planner"
        if role == "report":
            role = "reporter"
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
        planner_cfg = self.config.providers.get("planner")
        ptype = (
            planner_cfg.type
            if planner_cfg
            else (self.config.provider_prefs.primary or "openrouter")
        )
        return ProviderRoleConfig(type=ptype, model=primary, backup_model=backup)

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
        secrets = self.config.secrets
        if secrets.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY"):
            if self._get_or_create_provider("openrouter").available():
                return True
        for role in ("planner", "executor", "verifier"):
            rc = self._role_config(role)
            if self._get_or_create_provider(rc.type).available():
                return True
        return False

    def agentic_available(self) -> bool:
        """Whether supervisor tool-loop can reach an LLM."""
        if os.environ.get("WTH_TEST_MODE") == "1":
            return True
        rc = self._role_config("planner")
        return self._get_or_create_provider(rc.type).available()

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
        self._budget_gate(role)
        self.require_available()
        rc = self._role_config(role)
        providers_to_try = [rc.type]
        if self.config.provider_prefs.failover_enabled:
            for p in self.config.provider_prefs.failover_order:
                if p not in providers_to_try:
                    providers_to_try.append(p)
        last_err: Exception | None = None
        attempts: list[str] = []
        for ptype in providers_to_try:
            try:
                provider = self._get_or_create_provider(ptype)
                if not provider.available():
                    attempts.append(f"{ptype}: tidak tersedia")
                    continue
                primary = rc.model if ptype == rc.type else self._role_config(role).model
                backup = rc.backup_model or primary
                text, used_model = provider.chat_with_fallback(
                    primary, backup, messages,
                    timeout=timeout, stream=stream, on_token=on_token, response_format=response_format,
                )
                # Accounting runs on the SUCCESS path with the model that actually
                # answered, so a failover is billed to the backup model.
                self._record_usage(role, ptype, used_model, provider)
                self.last_finish_reason = getattr(provider, "last_finish_reason", "") or ""
                return text, used_model
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                attempts.append(f"{ptype}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
        if last_err:
            # Report EVERY attempt, not only the last one. The tail of
            # failover_order is usually the least informative (a local ollama
            # returning 404 on /api/chat), and raising it alone hid the real
            # cause -- an OpenRouter 400 "model not valid" -- behind a confusing
            # connection error. Verified live: the operator saw only the ollama
            # 404 while the actionable failure was the invalid model id.
            self.last_failures = attempts
            raise LLMError(
                f"semua provider gagal untuk role '{role}' — " + " | ".join(attempts)
            ) from last_err
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
        self._budget_gate(role)
        if os.environ.get("WTH_TEST_MODE") == "1":
            user = messages[-1].get("content", "") if messages else ""
            rc = self._role_config(role)
            model = rc.model
            if "scope" in user.lower() and "example.com" in user:
                return {
                    "message": {
                        "role": "assistant",
                        "content": "Scope set for example.com (test mode).",
                    },
                    "model": model,
                }
            return {
                "message": {
                    "role": "assistant",
                    "content": f"Test mode response. ({user[:80]})",
                },
                "model": model,
            }
        self.require_available()
        provider = self.provider_for_role(role)
        primary, backup = self.model_for_role(role)
        return provider.chat_with_tools(primary, messages, tools=tools, backup=backup, timeout=timeout)

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Canonical entry point (WU-109, PRD-013, strangler-fig).

        Facade over `chat_for_role()`/`chat_with_tools_for_role()` --
        reuses their WTH_TEST_MODE stub behavior, failover ordering, and
        provider resolution UNCHANGED, rather than reimplementing that logic
        here. `chat_for_role`/`chat_with_tools_for_role`/`complete`
        themselves do NOT become adapters over `generate()` in this WU (that
        would invert the dependency and touch code paths exercised by
        virtually every test in the suite -- deferred, see
        docs/planning/WORK-UNITS.md WU-109 scope decision). Never raises --
        errors are normalized into `ModelResponse.error` via
        `provider.normalize_error()`.
        """
        provider = self.provider_for_role(request.role)
        primary, _backup = self.model_for_role(request.role)
        try:
            if request.tools:
                data = self.chat_with_tools_for_role(
                    request.role, request.messages, tools=request.tools, timeout=request.timeout,
                )
                msg = data.get("message", {})
                return ModelResponse(
                    status="success",
                    provider=provider.name,
                    model=data.get("model", primary),
                    role=request.role,
                    content=msg.get("content") or "",
                    tool_calls=msg.get("tool_calls") or [],
                    raw=data,
                )
            content, used_model = self.chat_for_role(
                request.role, request.messages,
                timeout=request.timeout, stream=request.stream,
                on_token=request.on_token, response_format=request.response_format,
            )
            return ModelResponse(
                status="success",
                provider=provider.name,
                model=used_model,
                role=request.role,
                content=content,
                used_backup=used_model != primary,
            )
        except Exception as exc:  # noqa: BLE001 -- normalized into response
            return ModelResponse(
                status="error",
                provider=provider.name,
                model=primary,
                role=request.role,
                error=provider.normalize_error(exc),
            )

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
            "9router": s.router9_api_key,
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
            "9router": "router9_api_key",
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

    def test_provider(
        self,
        provider_type: str,
        model: str | None = None,
        *,
        completion_timeout: int = 30,
    ) -> dict[str, Any]:
        """Reachability + optional short completion, normalized."""
        p = self._get_or_create_provider(provider_type)
        conn = p.test_connection(model)
        result: dict[str, Any] = {
            "provider": provider_type,
            "reachable": conn["ok"],
            "reach_error": conn["error"],
            "error": None,
            "models_listed": 0,
            "completion_ok": False,
        }
        if not conn["ok"]:
            result["error"] = conn["error"]
            return result
        try:
            models = p.list_models()
            result["models_listed"] = len(models)
        except Exception as exc:  # noqa: BLE001
            result["error"] = p.normalize_error(exc)
        if model:
            env = p.complete(
                model, [{"role": "user", "content": "ping"}], timeout=completion_timeout, max_tokens=8,
            )
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

    def _primary_provider_type(self) -> str:
        planner_cfg = self.config.providers.get("planner")
        if planner_cfg:
            return planner_cfg.type
        return self.config.provider_prefs.primary or "openrouter"

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
        ptype = self._primary_provider_type()
        provider = self._get_or_create_provider(ptype)
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
        return self.chat_json_for_role("planner", messages, timeout=timeout)

    def chat_with_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        backup: str | None = None,
        timeout: int | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        role: str = "planner",
    ) -> dict[str, Any]:
        if os.environ.get("WTH_TEST_MODE") == "1":
            user = messages[-1].get("content", "") if messages else ""
            if "scope" in user.lower() and "example.com" in user:
                return {
                    "message": {
                        "role": "assistant",
                        "content": "Scope set for example.com (test mode).",
                    },
                    "model": model,
                }
            return {
                "message": {
                    "role": "assistant",
                    "content": f"Test mode response. Configure LLM for full agentic tools. ({user[:80]})",
                },
                "model": model,
            }
        return self.chat_with_tools_for_role(
            role, messages, tools=tools, timeout=timeout,
        )
