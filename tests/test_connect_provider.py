"""`/connect` — tambah endpoint OpenAI-compatible sebagai provider LLM.

Yang diuji di sini adalah kontrak yang membuat fitur ini tidak berbahaya:
URL divalidasi, API key masuk vault (bukan user.yaml), provider bisa dibangun
router, dan menghapus provider juga menghapus secret-nya.
"""

from __future__ import annotations

import builtins

import pytest
import yaml

from wth_ai import config as cfg_mod
from wth_ai.config import PROVIDER_NAMES, WTHConfig
from wth_ai.llm import connect as conn
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.vault import SecretVault


@pytest.fixture
def user_cfg(tmp_path, monkeypatch):
    path = tmp_path / "config" / "user.yaml"
    monkeypatch.setattr(cfg_mod, "USER_CONFIG_PATH", path)
    return path


# --------------------------------------------------------------------------- #
# normalisasi URL
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://127.0.0.1:1234", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1"),
        ("https://gateway.example.com/openai/v1", "https://gateway.example.com/openai/v1"),
        ("127.0.0.1:1234", "http://127.0.0.1:1234/v1"),
    ],
)
def test_normalize_base_url(raw, expected):
    assert conn.normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://host/v1", "http://", "https://"])
def test_normalize_base_url_rejects_junk(raw):
    with pytest.raises(ValueError):
        conn.normalize_base_url(raw)


def test_derive_name_is_slug_and_unique():
    assert conn.derive_name("http://127.0.0.1:1234/v1") == "127-0-0-1-1234"
    assert conn.derive_name("https://Gateway.Example.com/v1") == "gateway-example-com"
    assert conn.derive_name("http://127.0.0.1:1234/v1", existing={"127-0-0-1-1234"}) != "127-0-0-1-1234"


def test_derive_name_never_shadows_builtin_provider():
    name = conn.derive_name("https://openai.example.com/v1")
    assert name not in PROVIDER_NAMES


# --------------------------------------------------------------------------- #
# register / secret
# --------------------------------------------------------------------------- #
def test_connect_registers_provider_and_vaults_key(user_cfg):
    config = WTHConfig()
    result = conn.connect(
        config, base_url="http://127.0.0.1:9999/v1", api_key="sk-test-123456789", name="lab", probe=False
    )

    assert result.name == "lab"
    assert result.base_url == "http://127.0.0.1:9999/v1"
    stored = config.custom_providers["lab"]
    assert stored.base_url == "http://127.0.0.1:9999/v1"
    assert stored.api_key_ref == "vault:provider_lab_api_key"
    assert SecretVault().get("provider_lab_api_key") == "sk-test-123456789"


def test_connect_accepts_keyless_local_endpoint(user_cfg):
    config = WTHConfig()
    result = conn.connect(config, base_url="http://127.0.0.1:9999", name="local", probe=False)

    assert result.name == "local"
    assert config.custom_providers["local"].api_key_ref is None
    assert config.custom_providers["local"].base_url == "http://127.0.0.1:9999/v1"


def test_connect_rejects_name_that_collides_with_builtin(user_cfg):
    config = WTHConfig()
    with pytest.raises(ValueError):
        conn.connect(config, base_url="http://127.0.0.1:9999/v1", name="openai", probe=False)


def test_connect_rejects_bad_name(user_cfg):
    config = WTHConfig()
    with pytest.raises(ValueError):
        conn.connect(config, base_url="http://127.0.0.1:9999/v1", name="lab provider!", probe=False)


def test_persist_keeps_secret_out_of_user_yaml(user_cfg):
    config = WTHConfig()
    conn.connect(config, base_url="http://127.0.0.1:9999/v1", api_key="sk-plaintext-should-not-appear",
                 name="lab", model="lab-model", probe=False)
    config.save_user_config()

    raw = user_cfg.read_text(encoding="utf-8")
    assert "sk-plaintext-should-not-appear" not in raw
    data = yaml.safe_load(raw)
    assert data["custom_providers"]["lab"]["api_key_ref"] == "vault:provider_lab_api_key"
    assert data["custom_providers"]["lab"]["base_url"] == "http://127.0.0.1:9999/v1"
    assert data["custom_providers"]["lab"]["model"] == "lab-model"


def test_remove_provider_drops_entry_and_secret(user_cfg):
    config = WTHConfig()
    conn.connect(config, base_url="http://127.0.0.1:9999/v1", api_key="sk-remove-me-please", name="lab", probe=False)
    assert conn.disconnect(config, "lab") is True

    assert "lab" not in config.custom_providers
    assert SecretVault().get("provider_lab_api_key") is None
    assert conn.disconnect(config, "lab") is False


# --------------------------------------------------------------------------- #
# router
# --------------------------------------------------------------------------- #
def test_router_builds_provider_from_custom_config(user_cfg):
    config = WTHConfig()
    conn.connect(config, base_url="http://127.0.0.1:9999/v1", api_key="sk-router-key-1234", name="lab", probe=False)

    router = LLMProviderRouter(config)
    provider = router.provider_named("lab")

    assert provider.name == "lab"
    assert provider.base_url == "http://127.0.0.1:9999/v1"
    assert provider.api_key == "sk-router-key-1234"


def test_router_provider_named_after_removal_is_openrouter_fallback(user_cfg):
    config = WTHConfig()
    conn.connect(config, base_url="http://127.0.0.1:9999/v1", name="lab", probe=False)
    conn.disconnect(config, "lab")
    router = LLMProviderRouter(config)
    router.forget("lab")
    with pytest.raises(KeyError):
        router.provider_named("lab")


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #
def test_probe_reports_models(monkeypatch):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "alpha"}, {"id": "beta"}]}

    monkeypatch.setattr(conn.requests, "get", lambda *a, **k: _Resp())
    result = conn.probe_endpoint("http://127.0.0.1:9999/v1", "sk-x")

    assert result.ok is True
    assert result.models == ("alpha", "beta")


def test_probe_failure_is_reported_not_raised(monkeypatch):
    def _boom(*a, **k):
        raise conn.requests.RequestException("connection refused")

    monkeypatch.setattr(conn.requests, "get", _boom)
    result = conn.probe_endpoint("http://127.0.0.1:9999/v1", None)

    assert result.ok is False
    assert "connection refused" in result.detail


def test_connect_survives_failed_probe(monkeypatch, user_cfg):
    monkeypatch.setattr(conn, "probe_endpoint", lambda *a, **k: conn.ProbeResult(False, (), "unreachable"))
    config = WTHConfig()
    result = conn.connect(config, base_url="http://127.0.0.1:9999/v1", name="lab")

    assert result.probe_ok is False
    assert result.name in config.custom_providers


def test_probe_picks_first_model(monkeypatch, user_cfg):
    monkeypatch.setattr(conn, "probe_endpoint", lambda *a, **k: conn.ProbeResult(True, ("alpha", "beta"), ""))
    config = WTHConfig()
    result = conn.connect(config, base_url="http://127.0.0.1:9999/v1", name="lab")

    assert result.model == "alpha"
    assert config.custom_providers["lab"].model == "alpha"


# --------------------------------------------------------------------------- #
# handler /connect
# --------------------------------------------------------------------------- #
def test_handle_connect_without_args_non_interactive_shows_usage(monkeypatch):
    from wth_ai.cli.handlers import connect_handler

    monkeypatch.setattr(connect_handler, "interactive_available", lambda: False)
    out = connect_handler.handle_connect(_fake_orch(), [])

    assert "/connect" in out
    assert "Base URL" in out or "base URL" in out


def test_handle_connect_registers_then_lists(monkeypatch, user_cfg):
    from wth_ai.cli.handlers import connect_handler

    orch = _fake_orch()
    out = connect_handler.handle_connect(
        orch, ["http://127.0.0.1:9999/v1", "sk-handler-key-1234", "--name", "lab", "--no-probe"]
    )
    assert "lab" in out

    listing = connect_handler.handle_connect(orch, ["list"])
    assert "lab" in listing
    assert "http://127.0.0.1:9999/v1" in listing


def test_handle_connect_use_sets_all_roles(monkeypatch, user_cfg):
    from wth_ai.cli.handlers import connect_handler

    orch = _fake_orch()
    connect_handler.handle_connect(orch, ["http://127.0.0.1:9999/v1", "--name", "lab", "--no-probe"])
    out = connect_handler.handle_connect(orch, ["use", "lab", "lab-model"])

    assert "lab-model" in out
    for role in ("planner", "executor", "verifier"):
        assert orch.config.providers[role].type == "lab"
        assert orch.config.providers[role].model == "lab-model"


def test_wizard_asks_for_url_and_key_and_skips_model_when_probe_fails(monkeypatch, user_cfg):
    from wth_ai.cli.handlers import connect_handler

    monkeypatch.setattr(conn, "probe_endpoint", lambda *a, **k: conn.ProbeResult(False, (), "no route"))

    answers = iter(["http://127.0.0.1:9999/v1", "lab"])
    prompts: list[str] = []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(answers)

    orch = _fake_orch()
    out = connect_handler.run_connect_wizard(orch, prompt=fake_input, secret=lambda _p="": "sk-wizard-key-999",
                                             picker=lambda *a, **k: None)

    assert "lab" in out
    assert any("URL" in p or "url" in p for p in prompts)
    assert any("Nama" in p for p in prompts)
    assert orch.config.custom_providers["lab"].api_key_ref == "vault:provider_lab_api_key"


def test_platform_registry_exposes_connect():
    from wth_ai.platform.registry import get_platform_registry

    names = get_platform_registry().all_slash_names()
    assert "/connect" in names
    help_text = get_platform_registry().help_text(None, compact=False)
    assert "/connect" in help_text


def test_slash_router_registers_connect():
    from wth_ai.cli.slash_router import build_slash_router

    router = build_slash_router(_fake_orch(_ROUTER_ATTRS))
    assert "/connect" in router.handlers


_ROUTER_ATTRS = (
    "_handle_permission", "_handle_computer", "_handle_research", "_handle_jobs",
    "_handle_eval", "_handle_usage", "_handle_next", "_handle_payloads",
    "_handle_autopilot", "_handle_eval_act", "_assess_loop", "_run_hunt",
    "_execute_next", "_verify", "_report", "_handle_approve", "_run_intel",
    "_show_skill", "_browser_probe", "_handle_scope", "_status_summary",
    "_list_sessions", "resume", "new_session", "request_quit", "supervisor",
    "doctor", "findings_db", "asset_graph", "mcp", "state",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _fake_orch(extra: tuple[str, ...] = ()):
    """Objek minimal: handler hanya butuh config + client."""
    from types import SimpleNamespace

    config = WTHConfig()
    return SimpleNamespace(
        config=config,
        client=LLMProviderRouter(config),
        **{name: (lambda *_a, **_k: "") for name in extra},
    )