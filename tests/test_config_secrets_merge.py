"""Regression tests for the config write path and provider endpoint defaults.

The measured incident this file exists for:

1. `custom_base_url` was dropped from `~/.wth-ai/config/user.yaml`, while the
   vault REFERENCE for the API key survived -- because that reference is rebuilt
   from the vault's own key list, whereas a plaintext field is only written when
   the in-memory config carries it. `save_user_config()` REPLACED the file.
2. With no base URL, the `custom` provider fell back to
   `https://api.openai.com/v1`, so the operator's LAN gateway key was sent to
   OpenAI -- observed as `HTTP 401 ... Incorrect API key provided: sk-01088…5c4d`
   from openai.com. The visible symptom was "cannot pick a model": listing models
   returned nothing, so the picker had nothing to show.

Two invariants are locked here: a save must never erase a setting it does not
carry, and a provider must never inherit an endpoint it was not given.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import yaml

from wth_ai.config import WTHConfig
from wth_ai.llm.providers.openai_compat import (
    DEFAULT_BASE_URLS,
    OpenAICompatProvider,
    _normalize_base_url,
)


def _user_config(tmp_path, data: dict):
    path = tmp_path / "user.yaml"
    path.write_text(yaml.safe_dump(data, default_flow_style=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# save must merge, never wipe
# --------------------------------------------------------------------------- #
def test_save_never_drops_a_base_url_the_object_does_not_carry(tmp_path, monkeypatch):
    import wth_ai.config as config_mod

    path = _user_config(tmp_path, {
        "secrets": {"custom_base_url": "http://gw.lan:20128/v1",
                    "custom_api_key": "vault:custom_api_key"},
    })
    monkeypatch.setattr(config_mod, "USER_CONFIG_PATH", path)

    cfg = WTHConfig.load()
    # Simulate what happened in the wild: the in-memory object lost the URL (a
    # freshly built config, a wizard pass with an empty prompt, ...).
    cfg.secrets.custom_base_url = None
    cfg.save_user_config()

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["secrets"]["custom_base_url"] == "http://gw.lan:20128/v1", (
        "base URL hilang saat save -- ini yang mengarahkan key operator ke api.openai.com"
    )


def test_save_still_lets_a_new_value_win(tmp_path, monkeypatch):
    import wth_ai.config as config_mod

    path = _user_config(tmp_path, {"secrets": {"custom_base_url": "http://old.lan:1/v1"}})
    monkeypatch.setattr(config_mod, "USER_CONFIG_PATH", path)

    cfg = WTHConfig.load()
    cfg.secrets.custom_base_url = "http://new.lan:2/v1"
    cfg.save_user_config()

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["secrets"]["custom_base_url"] == "http://new.lan:2/v1"


def test_save_preserves_a_per_role_base_url_override(tmp_path, monkeypatch):
    import wth_ai.config as config_mod

    path = _user_config(tmp_path, {
        "providers": {"planner": {"type": "custom", "model": "auto",
                                  "base_url": "http://role-host.lan:20128/v1"}},
    })
    monkeypatch.setattr(config_mod, "USER_CONFIG_PATH", path)

    cfg = WTHConfig.load()
    cfg.providers["planner"].base_url = ""      # object no longer carries it
    cfg.save_user_config()

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["providers"]["planner"]["base_url"] == "http://role-host.lan:20128/v1"


def test_save_keeps_unrelated_sections_it_was_not_told_about(tmp_path, monkeypatch):
    """A full overwrite also used to risk dropping hand-written extras."""
    import wth_ai.config as config_mod

    path = _user_config(tmp_path, {"catatan_operator": "jangan dihapus"})
    monkeypatch.setattr(config_mod, "USER_CONFIG_PATH", path)

    cfg = WTHConfig.load()
    cfg.save_user_config()
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved.get("catatan_operator") == "jangan dihapus"


# --------------------------------------------------------------------------- #
# a provider must never inherit an endpoint it was not given
# --------------------------------------------------------------------------- #
def test_custom_without_a_url_never_becomes_openai():
    assert _normalize_base_url("custom", None) == ""
    assert _normalize_base_url("custom", "") == ""
    assert _normalize_base_url("custom", "   ") == ""
    assert "openai.com" not in _normalize_base_url("custom", None)


def test_known_providers_keep_their_defaults():
    assert _normalize_base_url("openai", None) == DEFAULT_BASE_URLS["openai"]
    assert _normalize_base_url("groq", None) == DEFAULT_BASE_URLS["groq"]
    assert _normalize_base_url("9router", "http://127.0.0.1:20128") == "http://127.0.0.1:20128/v1"


def test_explicit_url_still_wins_and_is_normalised():
    assert _normalize_base_url("custom", "http://gw.lan:20128/v1/") == "http://gw.lan:20128/v1"


def test_custom_with_a_key_but_no_url_is_unavailable():
    provider = OpenAICompatProvider(provider_name="custom", api_key="sk-lan-key", base_url="")
    assert provider.base_url == ""
    assert provider.available() is False, (
        "key tanpa endpoint tidak boleh dianggap siap -- itu yang mengirim key ke luar"
    )


def test_custom_with_url_is_available_even_keyless():
    provider = OpenAICompatProvider(provider_name="custom", api_key=None,
                                    base_url="http://gw.lan:20128/v1")
    assert provider.available() is True


def test_openai_provider_is_unaffected():
    provider = OpenAICompatProvider(provider_name="openai", api_key="sk-x")
    assert provider.base_url == DEFAULT_BASE_URLS["openai"]
    assert provider.available() is True


# --------------------------------------------------------------------------- #
# the empty catalog must say WHERE it asked
# --------------------------------------------------------------------------- #
class _Client:
    def __init__(self, endpoint: str):
        self._endpoint = endpoint

    def list_models(self, provider_type: str):
        return []

    def provider_for_role(self, role: str):
        return type("P", (), {"base_url": self._endpoint})()


class _Orch:
    def __init__(self, endpoint: str):
        self.client = _Client(endpoint)

        class _Cfg:
            class models:  # noqa: N801
                planner = "auto"

        self.config = _Cfg()

    def _persist(self):
        pass


def test_empty_catalog_names_the_endpoint_used():
    from wth_ai.cli.handlers.setup_handler import _model_list

    out = _model_list(_Orch("https://api.openai.com/v1"), "custom")
    assert "https://api.openai.com/v1" in out, (
        "tanpa endpoint di pesan, operator tidak tahu ke mana request-nya pergi"
    )
    assert "base URL" in out


def test_empty_catalog_without_an_endpoint_says_so():
    from wth_ai.cli.handlers.setup_handler import _model_list

    out = _model_list(_Orch(""), "custom")
    assert "belum diset" in out
