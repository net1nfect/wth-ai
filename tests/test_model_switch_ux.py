"""Tests for choosing a model out of a large provider catalog.

Measured context (verified live against the LAN gateway before writing this):
* `/model set planner <id>` DOES persist and the next call really uses it, even
  inside the running session -- the mechanism was never broken;
* but a gateway can list 268 ids, `/model list` printed a 272-line wall with no
  search, and NOTHING told the operator whether the model they just picked can
  actually answer. That is what "cannot pick another model" looked like from the
  outside.
So these tests lock the usability contract: search with matching indices,
probe-without-committing, and verification on select.
"""

from __future__ import annotations

import os

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import pytest

from wth_ai.config import ProviderRoleConfig
from wth_ai.cli.handlers.setup_handler import handle_model

CATALOG = [
    "auto", "deepseek-v4.1-flash-rr", "glm-5.3-flash-rr", "glm-4.6-air",
    "gh/copilot-search-a", "gpt-5.3-codex",
]


class _Provider:
    """Stub provider: answers only for models in `works`."""

    def __init__(self, works: set[str] | None = None):
        self.works = works if works is not None else {"glm-5.3-flash-rr"}
        self.probed: list[str] = []

    def complete(self, model, messages, *, role="planner", timeout=None, max_tokens=None):
        self.probed.append(model)
        if model in self.works:
            return {"provider": "custom", "model": model, "role": role,
                    "status": "success", "content": "pong", "usage": {}, "tool_calls": []}
        return {"provider": "custom", "model": model, "role": role, "status": "error",
                "content": "", "usage": {}, "tool_calls": [],
                "error": {"type": "timeout", "message": "Read timed out"}}


class _Config:
    def __init__(self):
        self.providers = {"planner": ProviderRoleConfig(type="custom", model="auto")}
        self.saved = 0

    def save_user_config(self):
        self.saved += 1


class _State:
    def __init__(self):
        self.metadata: dict = {}


class _Client:
    def __init__(self, provider):
        self._provider = provider
        self.set_roles: list[tuple] = []

    def list_models(self, provider_type: str):
        return list(CATALOG)

    def provider_for_role(self, role: str):
        return self._provider

    def set_role(self, role, provider, model, *, backup_model=None):
        self.set_roles.append((role, provider, model))


class _Orch:
    def __init__(self, provider=None):
        self.provider = provider or _Provider()
        self.config = _Config()
        self.state = _State()
        self.client = _Client(self.provider)

    def _persist(self):
        pass


@pytest.fixture(autouse=True)
def _no_verify_by_default(monkeypatch):
    """Verification spends a real call; unit tests opt back in explicitly."""
    monkeypatch.setenv("WTH_SKIP_MODEL_VERIFY", "1")


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def test_search_narrows_the_listing():
    out = handle_model(_Orch(), ["list", "glm"])
    assert "glm-5.3-flash-rr" in out and "glm-4.6-air" in out
    assert "gpt-5.3-codex" not in out
    assert "cocok" in out


def test_search_stores_exactly_what_was_shown_so_indices_match():
    """The numbers the operator reads must be the numbers `/model <n>` accepts."""
    orch = _Orch()
    handle_model(orch, ["list", "glm"])
    picker = orch.state.metadata["model_picker"]["models"]
    assert picker == ["glm-5.3-flash-rr", "glm-4.6-air"]
    handle_model(orch, ["1"])
    assert orch.config.providers["planner"].model == "glm-5.3-flash-rr"


def test_unknown_needle_explains_instead_of_printing_nothing():
    out = handle_model(_Orch(), ["list", "tidak-ada-model-ini"])
    assert "Tidak ada model cocok" in out


def test_browse_without_needle_still_points_at_search():
    out = handle_model(_Orch(), ["list"])
    assert "/model list <kata>" in out
    assert "1." in out


def test_provider_name_argument_still_selects_the_provider():
    """Backwards compatibility: `/model list 9router` is a provider, not a query."""
    orch = _Orch()
    out = handle_model(orch, ["list", "9router"])
    assert "9router" in out
    assert orch.state.metadata["model_picker"]["provider"] == "9router"


# --------------------------------------------------------------------------- #
# probe without committing
# --------------------------------------------------------------------------- #
def test_test_subcommand_probes_without_changing_config():
    orch = _Orch()
    before = orch.config.providers["planner"].model
    out = handle_model(orch, ["test", "glm-5.3-flash-rr"])
    assert "✓" in out and "merespons" in out
    assert orch.provider.probed == ["glm-5.3-flash-rr"]
    assert orch.config.providers["planner"].model == before, "probe tidak boleh mengubah config"
    assert orch.config.saved == 0


def test_test_subcommand_reports_a_model_that_does_not_answer():
    out = handle_model(_Orch(), ["test", "gpt-5.3-codex"])
    assert "TIDAK merespons" in out
    assert "Read timed out" in out


def test_test_accepts_a_number_from_the_last_listing():
    orch = _Orch()
    handle_model(orch, ["list", "glm"])
    out = handle_model(orch, ["test", "2"])
    assert "glm-4.6-air" in out
    assert orch.provider.probed[-1] == "glm-4.6-air"


def test_test_without_a_listing_explains_the_ordering():
    out = handle_model(_Orch(), ["test", "3"])
    assert "Belum ada daftar model" in out


def test_test_is_never_fooled_into_setting_a_model():
    orch = _Orch()
    handle_model(orch, ["test", "glm-5.3-flash-rr"])
    assert orch.client.set_roles == []


# --------------------------------------------------------------------------- #
# verification on select
# --------------------------------------------------------------------------- #
def test_select_verifies_and_says_so(monkeypatch):
    monkeypatch.delenv("WTH_SKIP_MODEL_VERIFY", raising=False)
    orch = _Orch()
    out = handle_model(orch, ["set", "planner", "glm-5.3-flash-rr"])
    assert "terverifikasi" in out
    assert "glm-5.3-flash-rr" in out
    assert orch.config.providers["planner"].model == "glm-5.3-flash-rr"


def test_select_warns_when_the_model_does_not_answer(monkeypatch):
    monkeypatch.delenv("WTH_SKIP_MODEL_VERIFY", raising=False)
    orch = _Orch()
    out = handle_model(orch, ["set", "planner", "gpt-5.3-codex"])
    assert "terpasang TAPI belum merespons" in out
    assert "/model test" in out, "harus menawarkan cara menguji kandidat lain"
    # setelan tetap tersimpan, tapi operator diberi tahu apa adanya
    assert orch.config.providers["planner"].model == "gpt-5.3-codex"


def test_verification_can_be_skipped(monkeypatch):
    monkeypatch.setenv("WTH_SKIP_MODEL_VERIFY", "1")
    orch = _Orch()
    out = handle_model(orch, ["set", "planner", "glm-5.3-flash-rr"])
    assert "verifikasi dilewati" in out
    assert orch.provider.probed == [], "tidak boleh ada call saat verifikasi dilewati"


def test_select_uses_the_canonical_set_role_api():
    """So any per-role cache in the router is invalidated, not bypassed."""
    orch = _Orch()
    handle_model(orch, ["set", "planner", "glm-5.3-flash-rr"])
    assert orch.client.set_roles == [("planner", "custom", "glm-5.3-flash-rr")]


def test_probe_survives_an_exploding_provider():
    class _Boom(_Provider):
        def complete(self, *a, **k):
            raise RuntimeError("provider meledak")

    orch = _Orch(_Boom())
    out = handle_model(orch, ["test", "apa-saja"])
    assert "TIDAK merespons" in out and "RuntimeError" in out


def test_select_by_number_also_verifies(monkeypatch):
    monkeypatch.delenv("WTH_SKIP_MODEL_VERIFY", raising=False)
    orch = _Orch()
    handle_model(orch, ["list", "glm"])
    out = handle_model(orch, ["1"])
    assert orch.config.providers["planner"].model == "glm-5.3-flash-rr"
    assert "terverifikasi" in out
