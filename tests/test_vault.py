"""Tests for the encrypted secret vault, migration, and workspace init."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

os.environ.setdefault("WTH_SKIP_SETUP", "1")
os.environ.setdefault("WTH_TEST_MODE", "1")

import yaml

from wth_ai.vault import (
    SecretVault,
    is_vault_ref,
    mask_secret,
    migrate_secrets,
    vault_ref,
)
from wth_ai.workspace import WORKSPACE_DIRS, init_workspace, workspace_status


def test_vault_set_get_list_delete():
    with tempfile.TemporaryDirectory() as tmp:
        vault = SecretVault(base_dir=Path(tmp))
        assert vault.list_keys() == []
        vault.set("openrouter_api_key", "sk-or-secret123")
        vault.set("telegram_bot_token", "123:abc")
        assert vault.get("openrouter_api_key") == "sk-or-secret123"
        assert vault.list_keys() == ["openrouter_api_key", "telegram_bot_token"]
        assert vault.has("telegram_bot_token")
        assert vault.delete("telegram_bot_token") is True
        assert vault.delete("telegram_bot_token") is False
        assert vault.list_keys() == ["openrouter_api_key"]


def test_vault_is_encrypted_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        vault = SecretVault(base_dir=Path(tmp))
        vault.set("openrouter_api_key", "sk-PLAINTEXT-NEVER")
        raw = (Path(tmp) / "vault.enc").read_bytes()
        assert b"sk-PLAINTEXT-NEVER" not in raw


def test_vault_file_permissions_600():
    with tempfile.TemporaryDirectory() as tmp:
        vault = SecretVault(base_dir=Path(tmp))
        vault.set("k", "v")
        for name in ("vault.enc", "vault.key"):
            mode = stat.S_IMODE((Path(tmp) / name).stat().st_mode)
            assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_mask_secret():
    assert mask_secret("sk-or-abcd1234") == "sk-o****1234"
    assert mask_secret("short") == "****"
    assert mask_secret(None) == ""


def test_vault_resolve_reference():
    with tempfile.TemporaryDirectory() as tmp:
        vault = SecretVault(base_dir=Path(tmp))
        vault.set("openrouter_api_key", "sk-resolved")
        assert is_vault_ref("vault:openrouter_api_key")
        assert not is_vault_ref("plain")
        assert vault.resolve(vault_ref("openrouter_api_key")) == "sk-resolved"
        assert vault.resolve("plainvalue") == "plainvalue"


def test_resolve_missing_vault_returns_none_without_creating_key():
    with tempfile.TemporaryDirectory() as tmp:
        vault = SecretVault(base_dir=Path(tmp))
        assert vault.resolve("vault:openrouter_api_key") is None
        # Resolving against an empty vault must not create the key file.
        assert not (Path(tmp) / "vault.key").exists()


def test_migrate_plaintext_user_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cfg_path = base / "config" / "user.yaml"
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(yaml.safe_dump({
            "setup_complete": True,
            "secrets": {"openrouter_api_key": "sk-plain-123", "ollama_base_url": "http://x"},
            "bots": {"telegram_token": "tg-plain", "telegram_chat_ids": [1]},
        }))
        env_path = base / ".env"
        vault = SecretVault(base_dir=base)

        report = migrate_secrets(cfg_path, env_path, vault)

        assert "openrouter_api_key" in report["migrated"]
        assert "telegram_bot_token" in report["migrated"]
        assert vault.get("openrouter_api_key") == "sk-plain-123"
        assert vault.get("telegram_bot_token") == "tg-plain"
        # Backup created and config rewritten to references.
        assert report["backups"]
        rewritten = cfg_path.read_text()
        assert "sk-plain-123" not in rewritten
        assert "vault:openrouter_api_key" in rewritten
        assert "vault:telegram_bot_token" in rewritten
        # Non-secret value preserved.
        assert "http://x" in rewritten


def test_migrate_env_file_and_strip():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cfg_path = base / "config" / "user.yaml"
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text("setup_complete: true\n")
        env_path = base / ".env"
        env_path.write_text(
            "OPENROUTER_API_KEY=sk-env-999\n# comment\nOTHER_VAR=keepme\n"
        )
        vault = SecretVault(base_dir=base)

        report = migrate_secrets(cfg_path, env_path, vault)

        assert vault.get("openrouter_api_key") == "sk-env-999"
        new_env = env_path.read_text()
        assert "sk-env-999" not in new_env
        assert "OTHER_VAR=keepme" in new_env  # non-secret env preserved
        # Backup of .env exists.
        assert any(".env.bak-" in b for b in report["backups"])


def test_migrate_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cfg_path = base / "config" / "user.yaml"
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(yaml.safe_dump({"secrets": {"openrouter_api_key": "sk-1"}}))
        env_path = base / ".env"
        vault = SecretVault(base_dir=base)

        first = migrate_secrets(cfg_path, env_path, vault)
        assert first["migrated"] == ["openrouter_api_key"]
        # Second run finds only references -> nothing to migrate.
        second = migrate_secrets(cfg_path, env_path, vault)
        assert second["migrated"] == []


def test_workspace_init_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "home"
        init_workspace(base)
        # Seed an existing config to ensure it is preserved on re-init.
        cfg = base / "config" / "user.yaml"
        cfg.write_text("setup_complete: true\n")
        init_workspace(base)  # second run must not clobber

        status = workspace_status(base)
        assert status["complete"] is True
        assert status["missing_dirs"] == []
        for sub in WORKSPACE_DIRS:
            assert (base / sub).is_dir()
        assert (base / "db.sqlite").exists()
        assert cfg.read_text() == "setup_complete: true\n"


def test_workspace_home_dir_permissions_700():
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "home"
        init_workspace(base)
        mode = stat.S_IMODE(base.stat().st_mode)
        assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_config_loads_vault_reference():
    """wth_ai must run normally after migration: refs resolve from vault."""
    import wth_ai.config as cfg_mod
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["WTH_HOME"] = str(base)
        vault = SecretVault(base_dir=base)
        vault.set("openrouter_api_key", "sk-from-vault")
        cfg_path = base / "config" / "user.yaml"
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(yaml.safe_dump({
            "secrets": {"openrouter_api_key": "vault:openrouter_api_key"},
        }))
        old = cfg_mod.USER_CONFIG_PATH
        cfg_mod.USER_CONFIG_PATH = cfg_path
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            cfg = cfg_mod.WTHConfig.load()
            assert cfg.secrets.openrouter_api_key == "sk-from-vault"
        finally:
            cfg_mod.USER_CONFIG_PATH = old
            os.environ.pop("WTH_HOME", None)
            os.environ.pop("OPENROUTER_API_KEY", None)
