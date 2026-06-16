"""First-run setup wizard — OpenClaw-style interactive configuration."""

from __future__ import annotations

import os
import sys
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from wth_ai import __version__
from wth_ai.config import USER_CONFIG_PATH, WTHConfig
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.vault import SecretVault
from wth_ai.workspace import init_workspace

console = Console(highlight=False)


def _store_secret(name: str, value: str) -> None:
    """Persist a secret to the encrypted vault (never plaintext config/log)."""
    if value:
        SecretVault().set(name, value)

PROVIDER_CHOICES = {
    "1": ("openrouter", "OpenRouter (recommended — semua model)"),
    "2": ("ollama", "Ollama (local)"),
    "3": ("openai", "OpenAI direct"),
    "4": ("anthropic", "Anthropic direct"),
    "5": ("google", "Google Gemini direct"),
}


def _pick_provider(label: str) -> str:
    console.print(f"\n[bold]{label}[/] — pilih provider:")
    for key, (_, desc) in PROVIDER_CHOICES.items():
        console.print(f"  {key}. {desc}")
    choice = Prompt.ask("Pilih", choices=list(PROVIDER_CHOICES.keys()), default="1")
    return PROVIDER_CHOICES[choice][0]


def _collect_api_key(provider_type: str, existing: WTHConfig) -> WTHConfig:
    if provider_type == "openrouter":
        key = Prompt.ask("OpenRouter API key", password=True, default=existing.secrets.openrouter_api_key or "")
        if key:
            existing.secrets.openrouter_api_key = key
            _store_secret("openrouter_api_key", key)
    elif provider_type == "openai":
        key = Prompt.ask("OpenAI API key", password=True, default=existing.secrets.openai_api_key or "")
        if key:
            existing.secrets.openai_api_key = key
            _store_secret("openai_api_key", key)
    elif provider_type == "anthropic":
        key = Prompt.ask("Anthropic API key", password=True, default=existing.secrets.anthropic_api_key or "")
        if key:
            existing.secrets.anthropic_api_key = key
            _store_secret("anthropic_api_key", key)
    elif provider_type == "google":
        key = Prompt.ask("Google API key", password=True, default=existing.secrets.google_api_key or "")
        if key:
            existing.secrets.google_api_key = key
            _store_secret("google_api_key", key)
    elif provider_type == "ollama":
        url = Prompt.ask("Ollama base URL", default=existing.secrets.ollama_base_url)
        existing.secrets.ollama_base_url = url
    return existing


def _pick_model(router: LLMProviderRouter, provider_type: str, role: str, default: str) -> str:
    models = router.list_models(provider_type)
    if models:
        console.print(f"\nModel tersedia untuk {role} ({len(models)} total, menampilkan 15 pertama):")
        for i, m in enumerate(models[:15], 1):
            console.print(f"  {i}. {m}")
        custom = Prompt.ask(f"Model {role}", default=default)
        return custom
    return Prompt.ask(f"Model {role} (ketik manual)", default=default)


def run_setup_wizard(*, reset: bool = False, non_interactive: dict[str, Any] | None = None) -> WTHConfig:
    """Run interactive setup; non_interactive dict for tests."""
    if reset and USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.unlink()

    init_workspace()
    config = WTHConfig.load()
    if non_interactive:
        return _apply_non_interactive(config, non_interactive)

    console.print(Panel(
        f"[bold]WTH-AI v{__version__} Setup[/]\n\n"
        "Konfigurasi pertama kali — semua eksekusi real-time, tanpa simulasi.\n"
        "Masukkan API key dan pilih model untuk setiap agent.",
        title="Setup Wizard",
        border_style="cyan",
    ))

    # Primary provider for all roles (can customize per role)
    same_for_all = Confirm.ask("Gunakan provider yang sama untuk planner, executor, verifier?", default=True)

    if same_for_all:
        ptype = _pick_provider("Semua agent")
        config = _collect_api_key(ptype, config)
        router = LLMProviderRouter(config)
        defaults = {
            "planner": config.models.planner,
            "executor": config.models.executor,
            "verifier": config.models.verifier,
        }
        for role in ("planner", "executor", "verifier"):
            model = _pick_model(router, ptype, role, defaults[role])
            if router.ping_provider(ptype, model):
                console.print(f"[green]✓[/] {role}: {ptype}/{model} — ping OK")
            else:
                console.print(f"[yellow]![/] {role}: ping gagal — tetap simpan, cek koneksi nanti")
            from wth_ai.config import ProviderRoleConfig
            config.providers[role] = ProviderRoleConfig(
                type=ptype, model=model,
            )
    else:
        for role in ("planner", "executor", "verifier"):
            ptype = _pick_provider(role.capitalize())
            config = _collect_api_key(ptype, config)
            router = LLMProviderRouter(config)
            default = getattr(config.models, role, config.models.planner)
            model = _pick_model(router, ptype, role, default)
            from wth_ai.config import ProviderRoleConfig
            config.providers[role] = ProviderRoleConfig(type=ptype, model=model)

    perm = Prompt.ask("Permission default", choices=["read_only", "active_test", "standard"], default="read_only")
    config.default_permission = perm

    if Confirm.ask("Konfigurasi Discord bot?", default=False):
        config.bots.discord_token = Prompt.ask("Discord bot token", password=True)
        _store_secret("discord_bot_token", config.bots.discord_token or "")
        guilds = Prompt.ask("Allowed guild IDs (comma-separated, kosong=semua)", default="")
        if guilds.strip():
            config.bots.discord_guild_ids = [int(x.strip()) for x in guilds.split(",") if x.strip()]
        channels = Prompt.ask("Allowed channel IDs (comma-separated, kosong=semua)", default="")
        if channels.strip():
            config.bots.discord_channel_ids = [int(x.strip()) for x in channels.split(",") if x.strip()]

    if Confirm.ask("Konfigurasi Telegram bot?", default=False):
        config.bots.telegram_token = Prompt.ask("Telegram bot token", password=True)
        _store_secret("telegram_bot_token", config.bots.telegram_token or "")
        chats = Prompt.ask("Allowed chat IDs (comma-separated, kosong=semua)", default="")
        if chats.strip():
            config.bots.telegram_chat_ids = [int(x.strip()) for x in chats.split(",") if x.strip()]

    config.setup_complete = True
    config.require_llm = True
    config.realtime_only = True
    config.save_user_config()

    console.print(Panel(
        f"Setup selesai!\nConfig: {USER_CONFIG_PATH}\nSecrets: {SecretVault().path} (encrypted)\n\n"
        "Jalankan: [bold]wth-ai[/] atau [bold]wth-ai bot discord[/]",
        title="Selesai",
        border_style="green",
    ))
    return config


def _apply_non_interactive(config: WTHConfig, data: dict[str, Any]) -> WTHConfig:
    from wth_ai.config import ProviderRoleConfig
    ptype = data.get("provider_type", "openrouter")
    model = data.get("model", config.models.planner)
    for role in ("planner", "executor", "verifier"):
        config.providers[role] = ProviderRoleConfig(type=ptype, model=model)
    if data.get("openrouter_api_key"):
        config.secrets.openrouter_api_key = data["openrouter_api_key"]
        _store_secret("openrouter_api_key", data["openrouter_api_key"])
    config.setup_complete = True
    config.require_llm = data.get("require_llm", False)
    config.save_user_config()
    return config


def ensure_setup(*, force: bool = False) -> WTHConfig:
    """Run setup wizard if not complete."""
    from wth_ai.config import is_setup_complete
    if os.environ.get("WTH_SKIP_SETUP") == "1":
        return WTHConfig.load()
    if force or not is_setup_complete():
        if not sys.stdin.isatty() and not force:
            return WTHConfig.load()
        return run_setup_wizard()
    return WTHConfig.load()
