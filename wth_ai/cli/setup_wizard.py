"""First-run setup wizard — OpenClaw-style interactive configuration."""

from __future__ import annotations

import os
import sys
from typing import Any

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from wth_ai import __version__
from wth_ai.cli.ui.theme import SEP
from wth_ai.config import ROLE_NAMES, USER_CONFIG_PATH, WTHConfig
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.vault import SecretVault
from wth_ai.workspace import init_workspace

console = Console(highlight=False)


def _section(title: str, body: str = "") -> None:
    console.print(f"\n── {title} ──")
    if body:
        console.print(body)


def _store_secret(name: str, value: str) -> None:
    """Persist a secret to the encrypted vault (never plaintext config/log)."""
    if value:
        SecretVault().set(name, value)

PROVIDER_CHOICES = {
    "0": ("9router", "9router (local gateway — recommended)"),
    "1": ("openrouter", "OpenRouter (semua model)"),
    "2": ("ollama", "Ollama (local)"),
    "3": ("openai", "OpenAI direct"),
    "4": ("anthropic", "Anthropic direct"),
    "5": ("google", "Google Gemini direct"),
    "6": ("groq", "Groq direct"),
    "7": ("custom", "Custom OpenAI-compatible endpoint"),
}


def _pick_provider(label: str) -> str:
    console.print(f"\n[bold]{label}[/] — pilih provider:")
    for key, (_, desc) in PROVIDER_CHOICES.items():
        console.print(f"  {key}. {desc}")
    choice = Prompt.ask("Pilih", choices=list(PROVIDER_CHOICES.keys()), default="0")
    return PROVIDER_CHOICES[choice][0]


def _collect_api_key(provider_type: str, existing: WTHConfig) -> WTHConfig:
    if provider_type == "9router":
        key = Prompt.ask("9router API key", password=True, default=existing.secrets.router9_api_key or "")
        if key:
            existing.secrets.router9_api_key = key
            _store_secret("router9_api_key", key)
        url = Prompt.ask("9router base URL", default=existing.secrets.router9_base_url)
        existing.secrets.router9_base_url = url
    elif provider_type == "openrouter":
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
    elif provider_type == "groq":
        key = Prompt.ask("Groq API key", password=True, default=existing.secrets.groq_api_key or "")
        if key:
            existing.secrets.groq_api_key = key
            _store_secret("groq_api_key", key)
    elif provider_type == "custom":
        key = Prompt.ask("Custom API key", password=True, default=existing.secrets.custom_api_key or "")
        if key:
            existing.secrets.custom_api_key = key
            _store_secret("custom_api_key", key)
        url = Prompt.ask("Custom base URL", default=existing.secrets.custom_base_url or "")
        existing.secrets.custom_base_url = url
    return existing


def _pick_model(router: LLMProviderRouter, provider_type: str, role: str, default: str) -> str:
    from wth_ai.llm.model_pick import resolve_model_choice

    models = router.list_models(provider_type)
    if models:
        console.print(f"\nModel tersedia untuk {role} ({len(models)} total, menampilkan 15 pertama):")
        for i, m in enumerate(models[:15], 1):
            console.print(f"  {i}. {m}")
        console.print("  [dim]Ketik nomor (1-15) atau paste model id lengkap[/]")
        custom = Prompt.ask(f"Model {role}", default=default)
        resolved = resolve_model_choice(custom, models[:15] if custom.isdigit() else models)
        if resolved != custom:
            console.print(f"  → menggunakan [cyan]{resolved}[/]")
        return resolved
    return Prompt.ask(f"Model {role} (ketik manual)", default=default)


def run_setup_wizard(*, reset: bool = False, non_interactive: dict[str, Any] | None = None) -> WTHConfig:
    """Run interactive setup; non_interactive dict for tests."""
    if reset and USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.unlink()

    init_workspace()
    config = WTHConfig.load()
    if non_interactive:
        return _apply_non_interactive(config, non_interactive)

    _section(
        f"WTH-AI v{__version__} Setup",
        "Konfigurasi pertama kali — semua eksekusi real-time, tanpa simulasi.\n"
        "Masukkan API key dan pilih model untuk setiap agent.",
    )

    # Primary provider for all roles (can customize per role)
    same_for_all = Confirm.ask("Gunakan provider yang sama untuk planner, executor, verifier?", default=True)

    from wth_ai.config import ProviderRoleConfig

    if same_for_all:
        ptype = _pick_provider("Semua agent")
        config = _collect_api_key(ptype, config)
        router = LLMProviderRouter(config)
        default = config.models.planner
        model = _pick_model(router, ptype, "semua agent", default)
        for role in ROLE_NAMES:
            config.providers[role] = ProviderRoleConfig(type=ptype, model=model)
        console.print(f"  → semua role memakai [cyan]{model}[/]")
    else:
        for role in ROLE_NAMES:
            ptype = _pick_provider(role.capitalize())
            config = _collect_api_key(ptype, config)
            router = LLMProviderRouter(config)
            default = getattr(config.models, role, config.models.planner)
            model = _pick_model(router, ptype, role, default)
            config.providers[role] = ProviderRoleConfig(type=ptype, model=model)

    _section("Agent mode", "Hybrid = ngobrol + tools saat diminta. Security = selalu tools.")
    agent_mode = Prompt.ask("Agent mode", choices=["hybrid", "chat", "security"], default="hybrid")
    config.agent.mode = agent_mode

    _section("Terminal UI", "live = panel animasi, stream = verbose scroll, quiet = minimal.")
    activity_mode = Prompt.ask("Activity display", choices=["live", "stream", "quiet"], default="live")
    config.ui.activity_mode = activity_mode
    config.ui.verbose = activity_mode != "quiet"
    config.ui.banner = Confirm.ask("Unicode banner on startup?", default=True)

    perm = Prompt.ask("Permission default", choices=["read_only", "standard", "full_lab"], default="read_only")
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

    if Confirm.ask("Jalankan smoke test provider sekarang?", default=True):
        from wth_ai.llm.smoke import format_smoke_report, run_smoke_test
        primary = config.providers.get("planner")
        if primary:
            completion_timeout = 60 if primary.type == "9router" else 30
            report = run_smoke_test(
                config, primary.type, model=primary.model, completion_timeout=completion_timeout,
            )
            console.print(format_smoke_report(report))

    config.setup_complete = True
    config.require_llm = True
    config.realtime_only = True
    config.save_user_config()

    console.print(
        f"\n✓ Setup selesai{SEP}config: {USER_CONFIG_PATH}{SEP}vault: {SecretVault().path}\n"
        "Mulai: wth-ai\n\n"
        "Quick start:\n"
        "  /target example.com\n"
        "  /model list\n"
        "  /ui show\n"
        "  /quit\n"
    )
    return config


def _apply_non_interactive(config: WTHConfig, data: dict[str, Any]) -> WTHConfig:
    from wth_ai.config import ProviderRoleConfig
    ptype = data.get("provider_type", "openrouter")
    model = data.get("model", config.models.planner)
    for role in ROLE_NAMES:
        config.providers[role] = ProviderRoleConfig(type=ptype, model=model)
    config.agent.mode = data.get("agent_mode", getattr(config.agent, "mode", "hybrid"))
    config.ui.activity_mode = data.get("activity_mode", getattr(config.ui, "activity_mode", "live"))
    config.ui.verbose = config.ui.activity_mode != "quiet"
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
