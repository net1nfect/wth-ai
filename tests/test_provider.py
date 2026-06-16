"""Fase 2 tests: provider adapters, router, role models, profiles, smoke, doctor."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

from wth_ai.config import PROVIDER_NAMES, ROLE_NAMES, ProviderRoleConfig, WTHConfig
from wth_ai.llm.providers.base import BaseLLMProvider
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.llm.smoke import run_smoke_test
from wth_ai.vault import SecretVault

_ENV_KEYS = (
    "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "GROQ_API_KEY", "CUSTOM_API_KEY",
)


@contextlib.contextmanager
def temp_home():
    """Hermetic WTH_HOME + patched config paths."""
    import wth_ai.config as cfg_mod
    from wth_ai.workspace import init_workspace
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        old = {
            "WTH_HOME": os.environ.get("WTH_HOME"),
            "cfg": cfg_mod.USER_CONFIG_PATH,
            "env": cfg_mod.USER_ENV_PATH,
        }
        saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
        os.environ["WTH_HOME"] = str(base)
        cfg_mod.USER_CONFIG_PATH = base / "config" / "user.yaml"
        cfg_mod.USER_ENV_PATH = base / ".env"
        init_workspace(base)
        try:
            yield base
        finally:
            cfg_mod.USER_CONFIG_PATH = old["cfg"]
            cfg_mod.USER_ENV_PATH = old["env"]
            if old["WTH_HOME"] is None:
                os.environ.pop("WTH_HOME", None)
            else:
                os.environ["WTH_HOME"] = old["WTH_HOME"]
            for k in _ENV_KEYS:
                os.environ.pop(k, None)
                if saved_env.get(k) is not None:
                    os.environ[k] = saved_env[k]


class FakeProvider(BaseLLMProvider):
    provider_type = "openrouter"
    tool_calling = True

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok

    def available(self) -> bool:
        return self.ok

    def list_models(self) -> list[str]:
        return ["fake/model-a", "fake/model-b"] if self.ok else []

    def chat(self, model, messages, **kwargs) -> str:  # type: ignore[override]
        if not self.ok:
            raise RuntimeError("401 unauthorized: bad api key")
        return "pong"


# --- provider / router ---

def test_provider_names_and_capabilities():
    config = WTHConfig.load()
    router = LLMProviderRouter(config)
    assert set(("openrouter", "groq", "custom", "ollama")).issubset(set(PROVIDER_NAMES))
    caps = router.provider_capabilities("openrouter")
    assert caps["tool_calling"] is True
    assert router.provider_capabilities("groq")["provider"] == "groq"


def test_groq_and_custom_providers_constructed():
    config = WTHConfig.load()
    config.secrets.groq_api_key = "gsk-x"
    config.secrets.custom_api_key = "ck-x"
    config.secrets.custom_base_url = "https://llm.local/v1"
    router = LLMProviderRouter(config)
    groq = router._get_or_create_provider("groq")
    custom = router._get_or_create_provider("custom")
    assert groq.name == "groq"
    assert custom.base_url == "https://llm.local/v1"


def test_router_roles_and_set_role():
    config = WTHConfig.load()
    router = LLMProviderRouter(config)
    assert router.roles() == ROLE_NAMES
    # All roles resolve to a non-empty default model.
    for role in ROLE_NAMES:
        assert router.role_config(role).model
    router.set_role("recon", "groq", "llama-3.3-70b-versatile")
    rc = router.role_config("recon")
    assert rc.type == "groq" and rc.model == "llama-3.3-70b-versatile"


def test_complete_envelope_normalizes_error():
    config = WTHConfig.load()
    router = LLMProviderRouter(config)
    router._providers["openrouter"] = FakeProvider(ok=False)
    env = router.complete("planner", [{"role": "user", "content": "hi"}])
    assert env["status"] == "error"
    assert env["error"]["type"] == "auth"
    assert env["provider"] == "openrouter"
    assert env["role"] == "planner"


def test_complete_envelope_success():
    config = WTHConfig.load()
    router = LLMProviderRouter(config)
    router._providers["openrouter"] = FakeProvider(ok=True)
    env = router.complete("planner", [{"role": "user", "content": "hi"}])
    assert env["status"] == "success"
    assert env["content"] == "pong"


# --- config save/load role mapping (no plaintext secret) ---

def test_provider_config_save_load_roles():
    with temp_home() as home:
        config = WTHConfig.load()
        router = LLMProviderRouter(config)
        router.set_role("planner", "openrouter", "x/model-a")
        router.set_role("code", "groq", "llama-3.3-70b-versatile")
        config.save_user_config()

        reloaded = WTHConfig.load()
        assert reloaded.providers["planner"].model == "x/model-a"
        assert reloaded.providers["code"].type == "groq"


def test_provider_add_list_remove_via_vault():
    with temp_home() as home:
        vault = SecretVault()
        vault.set("groq_api_key", "gsk-SECRET-123")
        config = WTHConfig.load()
        router = LLMProviderRouter(config)
        assert router.provider_key("groq") == "gsk-SECRET-123"
        assert router.provider_key("ollama") == "local"

        config.save_user_config()
        text = (home / "config" / "user.yaml").read_text()
        assert "gsk-SECRET-123" not in text
        assert "vault:groq_api_key" in text

        assert vault.delete("groq_api_key") is True
        assert vault.get("groq_api_key") is None


def test_model_set_keeps_secret_out_of_config():
    with temp_home() as home:
        SecretVault().set("groq_api_key", "gsk-NOPLAIN-9")
        config = WTHConfig.load()
        router = LLMProviderRouter(config)
        router.set_role("executor", "groq", "llama-3.3-70b-versatile")
        config.save_user_config()
        text = (home / "config" / "user.yaml").read_text()
        assert "gsk-NOPLAIN-9" not in text
        assert "groq/llama-3.3-70b-versatile" in text or "llama-3.3-70b-versatile" in text


# --- profiles ---

def test_profile_create_use_show_delete():
    with temp_home():
        from wth_ai import profiles
        path = profiles.create_profile("budget")
        assert path.exists()
        assert "budget" in profiles.list_profiles()

        data = profiles.get_profile("budget")
        assert data["models"]["planner"]["provider"] == "openrouter"

        profiles.apply_profile("high-quality")
        assert profiles.active_profile() == "high-quality"
        cfg = WTHConfig.load()
        assert cfg.providers["planner"].model == "anthropic/claude-3.5-sonnet"

        assert profiles.delete_profile("budget") is True


def test_profile_contains_no_secret():
    with temp_home():
        from wth_ai import profiles
        path = profiles.create_profile("default")
        text = path.read_text()
        assert "api_key" not in text
        assert "sk-" not in text
        assert "vault:" not in text


def test_profile_local_uses_ollama():
    with temp_home():
        from wth_ai import profiles
        profiles.apply_profile("local")
        cfg = WTHConfig.load()
        assert all(cfg.providers[r].type == "ollama" for r in ROLE_NAMES)


# --- smoke test ---

def test_smoke_test_success_mock():
    with temp_home():
        SecretVault().set("openrouter_api_key", "sk-or-test")
        config = WTHConfig.load()
        router = LLMProviderRouter(config)
        router._providers["openrouter"] = FakeProvider(ok=True)
        report = run_smoke_test(config, "openrouter", model="fake/model-a", router=router)
        assert report["ok"] is True
        names = {s["name"]: s["ok"] for s in report["steps"]}
        assert names["secret_readable"] and names["provider_reachable"]
        assert names["completion"] and names["role_models_valid"]


def test_smoke_test_failure_mock():
    with temp_home():
        config = WTHConfig.load()
        router = LLMProviderRouter(config)
        router._providers["openrouter"] = FakeProvider(ok=False)
        report = run_smoke_test(config, "openrouter", model="fake/model-a", router=router)
        assert report["ok"] is False
        names = {s["name"]: s["ok"] for s in report["steps"]}
        assert names["secret_readable"] is False
        assert names["provider_reachable"] is False
        # Error must still be normalized in shape.
        assert names["error_normalized"] is True


# --- doctor ---

def test_doctor_detects_missing_provider_key():
    with temp_home():
        config = WTHConfig.load()
        router = LLMProviderRouter(config)
        router.set_role("planner", "groq", "llama-3.3-70b-versatile")
        config.save_user_config()
        from wth_ai.tools.doctor import ToolDoctor
        ps = ToolDoctor(WTHConfig.load()).check_provider_secrets()
        assert "groq" in ps["missing_keys"]
        assert ps["ok"] is False


def test_doctor_detects_incomplete_role_model():
    config = WTHConfig.load()
    config.providers["planner"] = ProviderRoleConfig(type="openrouter", model="")
    from wth_ai.tools.doctor import ToolDoctor
    roles = ToolDoctor(config).check_roles()
    assert "planner" in roles["missing"]
    assert roles["complete"] is False


def test_doctor_full_report_has_provider_section():
    with temp_home():
        from wth_ai.tools.doctor import ToolDoctor
        report = ToolDoctor(WTHConfig.load()).full_report()
        assert "Providers & Roles" in report
        assert "Role models" in report
