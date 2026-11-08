"""P1-1: status LLM tidak boleh bilang siap tanpa provider yang bisa dipakai.

`LLMProviderRouter.available()` mengembalikan True untuk konfigurasi default:
`9router` tanpa key dengan base URL tebakan `127.0.0.1:20128` dianggap siap,
padahal tidak ada server di sana. UI mencetak "llm online".
"""

from __future__ import annotations

from wth_ai.config import WTHConfig


def _router(**overrides) -> object:
    from wth_ai.llm.router import LLMProviderRouter

    cfg = WTHConfig()
    for key, value in overrides.items():
        target, _, attr = key.partition("__")
        setattr(getattr(cfg, target), attr, value)
    cfg.providers.clear()
    cfg.custom_providers.clear()
    return LLMProviderRouter(cfg)


def test_default_config_without_key_is_blocked(monkeypatch):
    for name in ("OPENROUTER_API_KEY", "ROUTER9_API_KEY", "9ROUTER_API_KEY",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg = WTHConfig()
    cfg.secrets.openrouter_api_key = ""
    cfg.secrets.router9_api_key = ""
    cfg.providers.clear()
    cfg.custom_providers.clear()

    from wth_ai.llm.router import LLMProviderRouter

    assert LLMProviderRouter(cfg).llm_status() == "blocked"


def test_key_makes_it_configured_not_online(monkeypatch):
    """Punya key bukan berarti server menjawab — itu "configured", bukan "online"."""
    cfg = WTHConfig()
    cfg.secrets.openrouter_api_key = "sk-test"
    cfg.providers.clear()
    cfg.custom_providers.clear()
    cfg.provider_prefs.primary = "openrouter"

    from wth_ai.llm.router import LLMProviderRouter

    assert LLMProviderRouter(cfg).llm_status() == "configured"


def test_explicit_endpoint_is_configured():
    cfg = WTHConfig()
    cfg.secrets.openrouter_api_key = ""
    cfg.custom_providers.clear()
    cfg.secrets.router9_base_url = "http://gw.lan:20128/v1"
    cfg.providers.clear()

    from wth_ai.llm.router import LLMProviderRouter

    assert LLMProviderRouter(cfg).llm_status() == "configured"


def test_unresolved_vault_reference_is_not_a_key(monkeypatch):
    """`vault:<name>` yang tidak bisa diurai bukan kredensial (ditemukan saat
    menjalankan suite: config mesin ini menyimpan referensi vault)."""
    for name in ("ROUTER9_API_KEY", "9ROUTER_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    cfg = WTHConfig()
    cfg.secrets.router9_api_key = "vault:router9_api_key"
    cfg.secrets.openrouter_api_key = ""
    cfg.providers.clear()
    cfg.custom_providers.clear()

    from wth_ai.llm.router import LLMProviderRouter

    assert LLMProviderRouter(cfg).llm_status() == "blocked"


def test_probe_reports_online_and_offline(monkeypatch):
    cfg = WTHConfig()
    cfg.secrets.openrouter_api_key = "sk-test"
    cfg.providers.clear()
    cfg.custom_providers.clear()
    cfg.provider_prefs.primary = "openrouter"

    from wth_ai.llm.router import LLMProviderRouter

    router = LLMProviderRouter(cfg)
    monkeypatch.setattr(router, "ping_provider", lambda *_a, **_k: True)
    assert router.llm_status(probe=True) == "online"
    monkeypatch.setattr(router, "ping_provider", lambda *_a, **_k: False)
    assert router.llm_status(probe=True) == "offline"


def test_config_show_does_not_claim_online(monkeypatch):
    """Permukaan operator memakai `llm_status`, bukan `available()`."""
    from wth_ai.cli.handlers.config_handler import _config_show

    monkeypatch.setenv("WTH_TEST_MODE", "1")
    cfg = WTHConfig()
    cfg.secrets.openrouter_api_key = ""
    cfg.providers.clear()
    cfg.custom_providers.clear()

    class _Orch:
        config = cfg
        state = type("S", (), {"permission_mode": type("P", (), {"value": "read_only"})(),
                               "scope": type("Sc", (), {"target": ""})(),
                               "metadata": {}})()

        class client:
            @staticmethod
            def available() -> bool:
                return True

            @staticmethod
            def llm_status(*_a, **_k) -> str:
                return "configured"

    text = _config_show(_Orch())
    assert "- llm: configured" in text
    assert "online" not in text