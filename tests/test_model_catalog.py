"""Regression tests — WU-110: Model capability registry (PRD-013).

Bug asli terkonfirmasi (verifikasi source sebelum implementasi):
`OpenRouterProvider.list_models()` memanggil `GET /models` yang mengembalikan
objek model LENGKAP (context_length, pricing, supported_parameters) dari API
OpenRouter asli, TAPI ekstraksinya `[m["id"] for m in data.get("data", [])]`
membuang SEMUA field itu, cuma menyisakan ID string. `get_context_length()`/
`get_rate_limit_info()` (BaseLLMProvider) SELALU `None` -- tidak pernah
di-override provider manapun sebelum WU ini.

Fix: `list_models_detailed()` method BARU (additive, TIDAK mengubah
`list_models()` existing) mengembalikan objek penuh. `model_catalog.py`
modul baru -- dua tingkat resolusi: SEED (fallback hardcode, selalu ada)
lalu LIVE CACHE (`refresh_from_openrouter()`, menang atas seed).
`OpenRouterProvider.get_context_length(model)` sekarang benar-benar
mengonsultasi katalog, bukan `None` statis.

Scope decision: `get_rate_limit_info()` TIDAK di-wire (OpenRouter API tidak
expose rate-limit per-model di endpoint /models yang sama) -- draft PRD-013
hanya eksplisit menyebut context_length di acceptance criteria literal.
`router.py::provider_capabilities()` TIDAK diubah -- dia memanggil
`get_context_length()` TANPA argumen model (level provider, bukan level
role/model spesifik -- ambiguitas arsitektural nyata, bukan bug), jadi tetap
`None` di jalur itu, sesuai desain no-arg yang aman.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.llm import model_catalog
from wth_ai.llm.providers.openrouter import OpenRouterProvider


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    model_catalog.clear_live_cache()
    yield
    model_catalog.clear_live_cache()


# ---------------------------------------------------------------------------
# model_catalog.get() -- seed lookup
# ---------------------------------------------------------------------------

def test_get_known_seed_model_returns_capability():
    cap = model_catalog.get("anthropic/claude-3.5-sonnet")
    assert cap is not None
    assert cap.context_length == 200_000


def test_get_unknown_model_returns_none():
    assert model_catalog.get("totally/unknown-model-xyz") is None


def test_configured_default_role_models_have_seed_entries():
    """Acceptance-adjacent: model default ModelConfig (config.py) punya entry
    seed -- bukan cuma satu contoh anthropic yang di-hardcode buat lolos test."""
    from wth_ai.config import ModelConfig

    defaults = ModelConfig()
    for model_id in (defaults.planner, defaults.executor, defaults.executor_backup):
        assert model_catalog.get(model_id) is not None, f"{model_id} tidak ada di seed"


# ---------------------------------------------------------------------------
# refresh_from_openrouter() -- live cache menang atas seed
# ---------------------------------------------------------------------------

def test_refresh_populates_live_cache_and_wins_over_seed():
    fake_provider = MagicMock()
    fake_provider.list_models_detailed.return_value = [
        {
            "id": "anthropic/claude-3.5-sonnet",
            "context_length": 999_999,  # sengaja beda dari seed (200_000) -- buktikan live menang
            "pricing": {"prompt": "0.000005", "completion": "0.00002"},
            "supported_parameters": ["tools", "response_format"],
        },
    ]
    count = model_catalog.refresh_from_openrouter(fake_provider)
    assert count == 1
    cap = model_catalog.get("anthropic/claude-3.5-sonnet")
    assert cap.context_length == 999_999
    assert cap.cost_per_1k_in == pytest.approx(0.005)
    assert cap.cost_per_1k_out == pytest.approx(0.02)
    assert cap.supports_tools is True
    assert cap.supports_json is True


def test_refresh_adds_new_model_not_in_seed():
    fake_provider = MagicMock()
    fake_provider.list_models_detailed.return_value = [
        {"id": "brand/new-model", "context_length": 32_000, "pricing": {}, "supported_parameters": []},
    ]
    model_catalog.refresh_from_openrouter(fake_provider)
    cap = model_catalog.get("brand/new-model")
    assert cap is not None
    assert cap.context_length == 32_000
    assert cap.supports_tools is False


def test_refresh_never_raises_on_provider_exception():
    fake_provider = MagicMock()
    fake_provider.list_models_detailed.side_effect = RuntimeError("network down")
    count = model_catalog.refresh_from_openrouter(fake_provider)
    assert count == 0
    # seed tetap utuh, tidak korup
    assert model_catalog.get("anthropic/claude-3.5-sonnet") is not None


def test_refresh_skips_entries_without_id():
    fake_provider = MagicMock()
    fake_provider.list_models_detailed.return_value = [{"context_length": 1000}]
    count = model_catalog.refresh_from_openrouter(fake_provider)
    assert count == 0


# ---------------------------------------------------------------------------
# OpenRouterProvider -- get_context_length() acceptance criterion literal
# ---------------------------------------------------------------------------

def test_get_context_length_acceptance_criterion_literal():
    """PRD-013 acceptance criteria literal: get_context_length(
    "anthropic/claude-3.5-sonnet") mengembalikan angka nyata, bukan None."""
    provider = OpenRouterProvider(api_key="fake-key")
    assert provider.get_context_length("anthropic/claude-3.5-sonnet") == 200_000


def test_get_context_length_unknown_model_returns_none():
    provider = OpenRouterProvider(api_key="fake-key")
    assert provider.get_context_length("nonexistent/model") is None


def test_get_context_length_no_model_arg_returns_none():
    """Compatibility: provider_capabilities() (router.py) memanggil TANPA
    argumen model -- harus tetap None, tidak crash."""
    provider = OpenRouterProvider(api_key="fake-key")
    assert provider.get_context_length() is None


def test_base_provider_default_still_returns_none():
    """Compatibility: provider LAIN (Ollama/OpenAICompat) yang tidak
    override get_context_length() tetap None seperti sebelumnya."""
    from wth_ai.llm.providers.ollama import OllamaProvider

    provider = OllamaProvider(base_url="http://127.0.0.1:11434")
    assert provider.get_context_length("some-model") is None


# ---------------------------------------------------------------------------
# list_models_detailed() vs list_models() -- additive, tidak mengubah existing
# ---------------------------------------------------------------------------

def test_list_models_detailed_returns_full_objects():
    provider = OpenRouterProvider(api_key="fake-key")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "data": [{"id": "model/a", "context_length": 8000, "pricing": {"prompt": "0.000001"}}],
    }
    fake_response.raise_for_status = lambda: None
    with patch("requests.get", return_value=fake_response):
        detailed = provider.list_models_detailed()
    assert detailed == [{"id": "model/a", "context_length": 8000, "pricing": {"prompt": "0.000001"}}]


def test_list_models_still_returns_ids_only_unchanged():
    """Compatibility: list_models() existing TIDAK berubah -- masih list[str]
    murni, bukan objek penuh (banyak caller lain bergantung pada shape ini)."""
    provider = OpenRouterProvider(api_key="fake-key")
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "data": [{"id": "model/a", "context_length": 8000}, {"id": "model/b"}],
    }
    fake_response.raise_for_status = lambda: None
    with patch("requests.get", return_value=fake_response):
        ids = provider.list_models()
    assert ids == ["model/a", "model/b"]
    assert all(isinstance(x, str) for x in ids)


def test_list_models_detailed_no_api_key_returns_empty():
    provider = OpenRouterProvider(api_key=None)
    assert provider.list_models_detailed() == []


def test_list_models_detailed_never_raises_on_http_error():
    provider = OpenRouterProvider(api_key="fake-key")
    with patch("requests.get", side_effect=RuntimeError("connection refused")):
        assert provider.list_models_detailed() == []
