"""Encrypted secret vault for WTH-AI.

Secrets (provider API keys, bot tokens) are stored encrypted in
``~/.wth-ai/vault.enc`` using Fernet (AES-128-CBC + HMAC). The encryption key
is provided by a pluggable :class:`KeyProvider` so the local-file key can later
be swapped for an OS keyring without touching call sites.

Config files only ever store *references* of the form ``vault:<name>``; plaintext
secrets must never be written to config or logs.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from wth_ai.workspace import secure_dir, secure_file, wth_home

VAULT_REF_PREFIX = "vault:"

# Canonical vault secret names <-> environment variables.
ENV_TO_VAULT: dict[str, str] = {
    "ROUTER9_API_KEY": "router9_api_key",
    "9ROUTER_API_KEY": "router9_api_key",
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "OPENAI_API_KEY": "openai_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "GOOGLE_API_KEY": "google_api_key",
    "GROQ_API_KEY": "groq_api_key",
    "CUSTOM_API_KEY": "custom_api_key",
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "DISCORD_BOT_TOKEN": "discord_bot_token",
}
VAULT_TO_ENV: dict[str, str] = {v: k for k, v in ENV_TO_VAULT.items()}


def is_vault_ref(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(VAULT_REF_PREFIX)


def vault_ref(name: str) -> str:
    return f"{VAULT_REF_PREFIX}{name}"


def ref_name(value: str) -> str:
    return value[len(VAULT_REF_PREFIX):] if is_vault_ref(value) else value


def mask_secret(value: str | None) -> str:
    """Mask a secret for display, e.g. ``sk-or****abcd``."""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


class KeyProvider(ABC):
    """Supplies the Fernet key used to encrypt/decrypt the vault."""

    @abstractmethod
    def get_key(self) -> bytes:  # pragma: no cover - interface
        ...


class LocalMasterKeyProvider(KeyProvider):
    """Store the master key in ``vault.key`` (chmod 600) next to the vault.

    Designed to be a drop-in target for replacement by an OS-keyring provider.
    """

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path

    def get_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        secure_file(self.key_path)
        return key


class VaultError(RuntimeError):
    """Raised when the vault cannot be opened or decrypted."""


class SecretVault:
    """Fernet-encrypted key/value secret store."""

    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        key_provider: KeyProvider | None = None,
    ) -> None:
        self.base_dir = base_dir or wth_home()
        self.path = self.base_dir / "vault.enc"
        self.key_provider = key_provider or LocalMasterKeyProvider(self.base_dir / "vault.key")

    def _fernet(self) -> Fernet:
        try:
            return Fernet(self.key_provider.get_key())
        except (ValueError, TypeError) as exc:
            raise VaultError(f"invalid vault key: {exc}") from exc

    def _load(self) -> dict[str, str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return {}
        try:
            data = self._fernet().decrypt(self.path.read_bytes())
        except InvalidToken as exc:
            raise VaultError("vault.enc cannot be decrypted (wrong key?)") from exc
        try:
            obj = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise VaultError("vault.enc payload is corrupted") from exc
        return {str(k): str(v) for k, v in obj.items()}

    def _save(self, data: dict[str, str]) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        secure_dir(self.base_dir)
        token = self._fernet().encrypt(json.dumps(data).encode("utf-8"))
        self.path.write_bytes(token)
        secure_file(self.path)

    def exists(self) -> bool:
        return self.path.exists()

    def can_open(self) -> bool:
        try:
            self._load()
            return True
        except VaultError:
            return False

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._save(data)

    def get(self, key: str) -> str | None:
        return self._load().get(key)

    def has(self, key: str) -> bool:
        return key in self._load()

    def delete(self, key: str) -> bool:
        data = self._load()
        if key not in data:
            return False
        del data[key]
        self._save(data)
        return True

    def list_keys(self) -> list[str]:
        return sorted(self._load().keys())

    def resolve(self, value: str | None) -> str | None:
        """Resolve a possible ``vault:<name>`` reference to its plaintext value."""
        if not is_vault_ref(value):
            return value
        try:
            return self.get(ref_name(value))  # type: ignore[arg-type]
        except VaultError:
            return None


# config user.yaml field -> canonical vault secret name
_SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("openrouter_api_key", "openrouter_api_key"),
    ("openai_api_key", "openai_api_key"),
    ("anthropic_api_key", "anthropic_api_key"),
    ("google_api_key", "google_api_key"),
    ("groq_api_key", "groq_api_key"),
    ("custom_api_key", "custom_api_key"),
)
_BOT_FIELDS: tuple[tuple[str, str], ...] = (
    ("telegram_token", "telegram_bot_token"),
    ("discord_token", "discord_bot_token"),
)


def migrate_secrets(
    config_path: Path,
    env_path: Path,
    vault: SecretVault,
    *,
    make_backup: bool = True,
) -> dict[str, list[str]]:
    """Move plaintext secrets from ``user.yaml`` / ``.env`` into the vault.

    Backups are written before any file is rewritten; config is left holding
    only ``vault:<name>`` references and migrated secret lines are removed from
    ``.env``. Idempotent: re-running with no plaintext secrets is a no-op.
    """
    import yaml
    from datetime import datetime

    report: dict[str, list[str]] = {"migrated": [], "backups": []}

    found: dict[str, str] = {}
    cfg: dict[str, object] = {}
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        sec = cfg.get("secrets") or {}
        for field, vname in _SECRET_FIELDS:
            val = sec.get(field) if isinstance(sec, dict) else None
            if val and not is_vault_ref(val):
                found[vname] = val
        bots = cfg.get("bots") or {}
        for field, vname in _BOT_FIELDS:
            val = bots.get(field) if isinstance(bots, dict) else None
            if val and not is_vault_ref(val):
                found[vname] = val

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in ENV_TO_VAULT and v:
                found.setdefault(ENV_TO_VAULT[k], v)

    if not found:
        return report

    for vname, val in found.items():
        if not vault.has(vname):
            vault.set(vname, val)
        report["migrated"].append(vname)
    report["migrated"].sort()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if make_backup:
        if config_path.exists():
            bak = config_path.parent / f"{config_path.name}.bak-{ts}"
            bak.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
            secure_file(bak)
            report["backups"].append(str(bak))
        if env_path.exists():
            bak = env_path.parent / f"{env_path.name}.bak-{ts}"
            bak.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
            secure_file(bak)
            report["backups"].append(str(bak))

    # Rewrite user.yaml secrets/bots to vault references.
    if config_path.exists():
        sec = cfg.get("secrets") if isinstance(cfg.get("secrets"), dict) else {}
        for field, vname in _SECRET_FIELDS:
            if vname in found:
                sec[field] = vault_ref(vname)  # type: ignore[index]
        if sec:
            cfg["secrets"] = sec
        bots = cfg.get("bots") if isinstance(cfg.get("bots"), dict) else {}
        for field, vname in _BOT_FIELDS:
            if vname in found:
                bots[field] = vault_ref(vname)  # type: ignore[index]
        if bots:
            cfg["bots"] = bots
        config_path.write_text(yaml.safe_dump(cfg, default_flow_style=False), encoding="utf-8")
        secure_file(config_path)

    # Drop migrated secret lines from .env (backup already taken).
    if env_path.exists():
        kept: list[str] = []
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in ENV_TO_VAULT and ENV_TO_VAULT[k] in found:
                    continue
            kept.append(line)
        env_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        secure_file(env_path)

    return report
