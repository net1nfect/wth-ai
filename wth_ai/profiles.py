"""Profile system — named presets of provider + per-role model mapping.

Profiles live in ``~/.wth-ai/profiles/<name>.yaml`` and never contain secrets
(only provider types + model ids). Applying a profile writes the role mapping
and light safety/tuning settings into ``user.yaml`` via the normal config saver,
so secrets stay in the vault.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wth_ai.config import ROLE_NAMES, ModelConfig, ProviderRoleConfig, WTHConfig
from wth_ai.workspace import wth_home


def profiles_dir() -> Path:
    return wth_home() / "profiles"


def _active_marker() -> Path:
    return profiles_dir() / ".active"


def _roles_for(provider: str, models: dict[str, str]) -> dict[str, dict[str, str]]:
    return {role: {"provider": provider, "model": models[role]} for role in ROLE_NAMES}


def _default_models() -> dict[str, str]:
    mc = ModelConfig()
    return {
        "planner": mc.planner,
        "recon": mc.recon,
        "executor": mc.executor,
        "code": mc.code,
        "verifier": mc.verifier,
        "reporter": mc.reporter,
        "fallback": mc.fallback,
    }


def builtin_presets() -> dict[str, dict[str, Any]]:
    """Return the built-in profile presets (role mapping has no secrets)."""
    defaults = _default_models()
    hq = {
        "planner": "anthropic/claude-3.5-sonnet",
        "recon": "anthropic/claude-3.5-sonnet",
        "executor": "qwen/qwen3-coder",
        "code": "qwen/qwen3-coder",
        "verifier": "anthropic/claude-3.5-sonnet",
        "reporter": "anthropic/claude-3.5-sonnet",
        "fallback": "openai/gpt-4o-mini",
    }
    local = {role: "llama3.1" for role in ROLE_NAMES}
    return {
        "default": {
            "active_provider": "openrouter",
            "models": _roles_for("openrouter", defaults),
            "safety": {"default_permission": "read_only"},
            "tuning": {"max_tokens": 8192, "temperature": 0.3},
        },
        "budget": {
            "active_provider": "openrouter",
            "models": _roles_for("openrouter", defaults),
            "safety": {"default_permission": "read_only"},
            "tuning": {"max_tokens": 4096, "temperature": 0.2},
        },
        "high-quality": {
            "active_provider": "openrouter",
            "models": _roles_for("openrouter", hq),
            "safety": {"default_permission": "standard"},
            "tuning": {"max_tokens": 8192, "temperature": 0.3},
        },
        "local": {
            "active_provider": "ollama",
            "models": _roles_for("ollama", local),
            "safety": {"default_permission": "read_only"},
            "tuning": {"max_tokens": 4096, "temperature": 0.3},
        },
        "recon-only": {
            "active_provider": "openrouter",
            "models": _roles_for("openrouter", defaults),
            "safety": {"default_permission": "read_only"},
            "tuning": {"max_tokens": 8192, "temperature": 0.3},
        },
        "bug-bounty": {
            "active_provider": "9router",
            "engagement_mode": "bug_bounty",
            "models": _roles_for("9router", defaults),
            # WU-003: require_authorized (dimensi 1, target authorization) SENGAJA
            # tidak lagi bisa di-set preset -- itu keputusan per-engagement manusia,
            # bukan pilihan template. unrestricted di sini HANYA mempengaruhi
            # capability ceiling (dimensi 2), tidak lagi membypass authorization.
            "safety": {"default_permission": "full_lab", "unrestricted": True},
            "recon": {"profile": "high-quality", "rate_limit_rps": 0},
            "tuning": {"max_tokens": 8192, "temperature": 0.3},
        },
        "ctf-aggressive": {
            "active_provider": "9router",
            "engagement_mode": "ctf",
            "models": _roles_for("9router", defaults),
            # WU-003: require_authorized (dimensi 1, target authorization) SENGAJA
            # tidak lagi bisa di-set preset -- itu keputusan per-engagement manusia,
            # bukan pilihan template. unrestricted di sini HANYA mempengaruhi
            # capability ceiling (dimensi 2), tidak lagi membypass authorization.
            "safety": {"default_permission": "full_lab", "unrestricted": True},
            "recon": {"profile": "high-quality", "rate_limit_rps": 0},
            "tuning": {"max_tokens": 8192, "temperature": 0.4},
        },
    }


def list_profiles() -> list[str]:
    d = profiles_dir()
    on_disk = sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []
    # Built-in presets are always offerable even before being materialized.
    return sorted(set(on_disk) | set(builtin_presets().keys()))


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{name}.yaml"


def get_profile(name: str) -> dict[str, Any] | None:
    path = profile_path(name)
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return builtin_presets().get(name)


def create_profile(name: str, data: dict[str, Any] | None = None) -> Path:
    """Materialize a profile to disk. Uses a built-in preset when data is None."""
    profiles_dir().mkdir(parents=True, exist_ok=True)
    if data is None:
        data = builtin_presets().get(name)
        if data is None:
            raise ValueError(f"unknown preset: {name}")
    payload = {"name": name, **data}
    path = profile_path(name)
    path.write_text(yaml.safe_dump(payload, default_flow_style=False), encoding="utf-8")
    return path


def delete_profile(name: str) -> bool:
    path = profile_path(name)
    if path.exists():
        path.unlink()
        if active_profile() == name:
            _active_marker().unlink(missing_ok=True)
        return True
    return False


def active_profile() -> str | None:
    marker = _active_marker()
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip() or None
    return None


def apply_profile(name: str) -> dict[str, Any]:
    """Apply a profile to user.yaml: role mapping + permission + tuning.

    Secrets are untouched (remain vault references). Returns the applied data.
    """
    data = get_profile(name)
    if data is None:
        raise ValueError(f"unknown profile: {name}")

    config = WTHConfig.load()
    models = data.get("models", {})
    config.providers = {
        role: ProviderRoleConfig(
            type=spec.get("provider", "openrouter"),
            model=spec.get("model", ""),
            backup_model=spec.get("backup_model"),
            base_url=spec.get("base_url"),
        )
        for role, spec in models.items()
    }
    safety = data.get("safety", {})
    if safety.get("default_permission"):
        config.default_permission = safety["default_permission"]
        config.safety.default_permission = safety["default_permission"]
    if "unrestricted" in safety:
        config.safety.unrestricted = bool(safety["unrestricted"])
    if "require_authorized" in safety:
        config.safety.require_authorized = bool(safety["require_authorized"])
    if data.get("engagement_mode"):
        from wth_ai.engagement.mode import set_engagement_mode
        set_engagement_mode(data["engagement_mode"])
    if config.safety.unrestricted:
        from wth_ai.engagement.mode import set_unrestricted
        set_unrestricted(True)
    tuning = data.get("tuning", {})
    if "max_tokens" in tuning:
        config.models.max_tokens = int(tuning["max_tokens"])
    if "temperature" in tuning:
        config.models.temperature = float(tuning["temperature"])
    config.save_user_config()

    profiles_dir().mkdir(parents=True, exist_ok=True)
    _active_marker().write_text(name, encoding="utf-8")
    return data
