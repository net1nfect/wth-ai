#!/usr/bin/env python3
"""WTH-AI v1.0 — Realtime Bug Hunter CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console

_ROOT = Path(__file__).resolve().parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

_WTH_HOME = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
load_dotenv(_WTH_HOME / ".env")
load_dotenv(_ROOT / ".env")

from wth_ai import __version__
from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


def _load_config(config_path: str) -> WTHConfig:
    return WTHConfig.load(config_path)


def _orch(config_path: str, *, fresh: bool = False) -> WTHOrchestrator:
    return WTHOrchestrator(_load_config(config_path), fresh=fresh)


@click.group(invoke_without_command=True)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
@click.option("--version", is_flag=True, help="Show version.")
@click.option("--skip-setup", is_flag=True, help="Skip first-run setup check.")
@click.pass_context
def cli(ctx: click.Context, config_path: str, version: bool, skip_setup: bool) -> None:
    """WTH-AI v1.0 — realtime bug hunter with dual-agent AI."""
    if version:
        click.echo(f"WTH-AI v{__version__}")
        return
    if skip_setup:
        os.environ["WTH_SKIP_SETUP"] = "1"
    if ctx.invoked_subcommand is None:
        from wth_ai.cli.setup_wizard import ensure_setup
        ensure_setup()
        _orch(config_path).run_interactive()


@cli.command()
@click.option("--reset", is_flag=True, help="Reset existing configuration.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def setup(config_path: str, reset: bool) -> None:
    """Run first-time setup wizard (API keys, models, bots)."""
    from wth_ai.cli.setup_wizard import run_setup_wizard
    run_setup_wizard(reset=reset)


@cli.command()
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def doctor(config_path: str) -> None:
    """Diagnose workspace, vault, config, and tool health."""
    from wth_ai.tools.doctor import ToolDoctor
    click.echo(ToolDoctor(_load_config(config_path)).full_report())


@cli.group()
def vault() -> None:
    """Manage the encrypted secret vault (~/.wth-ai/vault.enc)."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@vault.command("set")
@click.argument("key")
@click.argument("value", required=False)
def vault_set(key: str, value: str | None) -> None:
    """Store a secret. Prompts (hidden) if VALUE is omitted."""
    from wth_ai.vault import SecretVault, mask_secret
    if value is None:
        value = click.prompt(f"Value for {key}", hide_input=True)
    SecretVault().set(key, value)
    click.echo(f"Stored {key} = {mask_secret(value)}")


@vault.command("get")
@click.argument("key")
@click.option("--reveal", is_flag=True, help="Print the full secret (use with care).")
def vault_get(key: str, reveal: bool) -> None:
    """Show a secret (masked by default)."""
    from wth_ai.vault import SecretVault, mask_secret
    val = SecretVault().get(key)
    if val is None:
        click.echo(f"{key}: not found")
        return
    click.echo(f"{key}: {val if reveal else mask_secret(val)}")


@vault.command("list")
def vault_list() -> None:
    """List secret names (values are never shown)."""
    from wth_ai.vault import SecretVault
    keys = SecretVault().list_keys()
    click.echo("\n".join(keys) if keys else "(vault empty)")


@vault.command("delete")
@click.argument("key")
def vault_delete(key: str) -> None:
    """Delete a secret."""
    from wth_ai.vault import SecretVault
    ok = SecretVault().delete(key)
    click.echo(f"Deleted {key}" if ok else f"{key}: not found")


@vault.command("migrate")
def vault_migrate() -> None:
    """Migrate plaintext secrets from user.yaml/.env into the vault."""
    from wth_ai.config import USER_CONFIG_PATH, USER_ENV_PATH
    from wth_ai.vault import SecretVault, migrate_secrets
    from wth_ai.workspace import init_workspace
    init_workspace()
    report = migrate_secrets(USER_CONFIG_PATH, USER_ENV_PATH, SecretVault())
    if not report["migrated"]:
        click.echo("No plaintext secrets found — nothing to migrate.")
        return
    click.echo(f"Migrated to vault: {', '.join(report['migrated'])}")
    for b in report["backups"]:
        click.echo(f"Backup: {b}")


# Provider name -> vault secret key (ollama is local, no key).
_PROVIDER_VAULT_KEY = {
    "openrouter": "openrouter_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "google": "google_api_key",
    "groq": "groq_api_key",
    "custom": "custom_api_key",
}


@cli.group()
def provider() -> None:
    """Manage LLM providers (keys live in the vault)."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@provider.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_list(config_path: str) -> None:
    """List supported providers and key/availability status."""
    from wth_ai.config import PROVIDER_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    from wth_ai.vault import mask_secret
    router = LLMProviderRouter(_load_config(config_path))
    for name in PROVIDER_NAMES:
        key = router.provider_key(name)
        if name == "ollama":
            status = "local"
        else:
            status = mask_secret(key) if key else "no key"
        caps = router.provider_capabilities(name)
        tools = "tools" if caps["tool_calling"] else "no-tools"
        click.echo(f"- {name}: {status} ({tools})")


@provider.command("add")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_add(name: str, config_path: str) -> None:
    """Add/update a provider: store its API key (and base URL) securely."""
    from wth_ai.config import PROVIDER_NAMES
    from wth_ai.vault import SecretVault
    if name not in PROVIDER_NAMES:
        click.echo(f"Unknown provider '{name}'. Choices: {', '.join(PROVIDER_NAMES)}")
        return
    config = _load_config(config_path)
    if name == "ollama":
        url = click.prompt("Ollama base URL", default=config.secrets.ollama_base_url)
        config.secrets.ollama_base_url = url
    else:
        key = click.prompt(f"{name} API key", hide_input=True)
        SecretVault().set(_PROVIDER_VAULT_KEY[name], key)
        if name == "custom":
            config.secrets.custom_base_url = click.prompt("Custom base URL (OpenAI-compatible)")
    config.save_user_config()
    click.echo(f"Provider '{name}' configured.")


@provider.command("remove")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_remove(name: str, config_path: str) -> None:
    """Remove a provider's stored secret."""
    from wth_ai.vault import SecretVault
    vkey = _PROVIDER_VAULT_KEY.get(name)
    if not vkey:
        click.echo(f"Nothing to remove for '{name}'.")
        return
    ok = SecretVault().delete(vkey)
    config = _load_config(config_path)
    config.save_user_config()
    click.echo(f"Removed secret for '{name}'." if ok else f"No secret stored for '{name}'.")


@provider.command("test")
@click.argument("name")
@click.option("--model", default=None, help="Optional model for a short completion test.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_test(name: str, model: str | None, config_path: str) -> None:
    """Test provider reachability (and optional completion)."""
    from wth_ai.llm.router import LLMProviderRouter
    result = LLMProviderRouter(_load_config(config_path)).test_provider(name, model)
    click.echo(json.dumps(result, indent=2, default=str))


@provider.command("models")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_models(name: str, config_path: str) -> None:
    """List models offered by a provider."""
    from wth_ai.llm.router import LLMProviderRouter
    models = LLMProviderRouter(_load_config(config_path)).list_models(name)
    click.echo("\n".join(models) if models else "(no models returned)")


@cli.group()
def model() -> None:
    """Inspect and set per-role models."""


@model.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def model_list(config_path: str) -> None:
    """Show the model assigned to each agent role."""
    from wth_ai.config import ROLE_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    router = LLMProviderRouter(_load_config(config_path))
    for role in ROLE_NAMES:
        rc = router.role_config(role)
        click.echo(f"- {role:9s} {rc.type}/{rc.model}")


@model.command("set")
@click.argument("role")
@click.argument("provider_name")
@click.argument("model_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def model_set(role: str, provider_name: str, model_id: str, config_path: str) -> None:
    """Set ROLE to use PROVIDER_NAME/MODEL_ID."""
    from wth_ai.config import PROVIDER_NAMES, ROLE_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    from wth_ai.workspace import init_workspace
    if role not in ROLE_NAMES:
        click.echo(f"Unknown role '{role}'. Choices: {', '.join(ROLE_NAMES)}")
        return
    if provider_name not in PROVIDER_NAMES:
        click.echo(f"Unknown provider '{provider_name}'. Choices: {', '.join(PROVIDER_NAMES)}")
        return
    init_workspace()
    config = _load_config(config_path)
    router = LLMProviderRouter(config)
    router.set_role(role, provider_name, model_id)
    config.save_user_config()
    click.echo(f"Set {role} -> {provider_name}/{model_id}")


@model.command("test")
@click.argument("role")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def model_test(role: str, config_path: str) -> None:
    """Run a short completion for ROLE and show the normalized result."""
    from wth_ai.config import ROLE_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    if role not in ROLE_NAMES:
        click.echo(f"Unknown role '{role}'. Choices: {', '.join(ROLE_NAMES)}")
        return
    router = LLMProviderRouter(_load_config(config_path))
    env = router.complete(role, [{"role": "user", "content": "ping"}], timeout=30, max_tokens=8)
    click.echo(json.dumps(env, indent=2, default=str))


@cli.group()
def profile() -> None:
    """Manage configuration profiles (presets of provider + role models)."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@profile.command("list")
def profile_list() -> None:
    """List available profiles (built-in presets + user-created)."""
    from wth_ai.profiles import active_profile, list_profiles
    active = active_profile()
    for name in list_profiles():
        mark = " *" if name == active else ""
        click.echo(f"- {name}{mark}")


@profile.command("create")
@click.argument("name")
def profile_create(name: str) -> None:
    """Create a profile from a built-in preset (or empty default)."""
    from wth_ai.profiles import create_profile
    try:
        path = create_profile(name)
    except ValueError:
        from wth_ai.profiles import builtin_presets
        path = create_profile(name, builtin_presets()["default"])
    click.echo(f"Created profile '{name}' at {path}")


@profile.command("use")
@click.argument("name")
def profile_use(name: str) -> None:
    """Apply a profile to the active config (secrets untouched)."""
    from wth_ai.profiles import apply_profile
    try:
        apply_profile(name)
    except ValueError as exc:
        click.echo(str(exc))
        return
    click.echo(f"Active profile: {name}")


@profile.command("show")
@click.argument("name")
def profile_show(name: str) -> None:
    """Show a profile's contents."""
    import yaml as _yaml
    from wth_ai.profiles import get_profile
    data = get_profile(name)
    if data is None:
        click.echo(f"Profile '{name}' not found.")
        return
    click.echo(_yaml.safe_dump(data, default_flow_style=False))


@profile.command("delete")
@click.argument("name")
def profile_delete(name: str) -> None:
    """Delete a user-created profile."""
    from wth_ai.profiles import delete_profile
    click.echo(f"Deleted '{name}'." if delete_profile(name) else f"Profile '{name}' not found on disk.")


@cli.group()
def integration() -> None:
    """Manage Telegram/Discord/Webhook integrations (event-driven)."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@integration.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def integration_list(config_path: str) -> None:
    """List integration adapters and their status (tokens masked)."""
    from wth_ai.integrations.manager import IntegrationManager
    mgr = IntegrationManager(_load_config(config_path))
    for st in mgr.status():
        click.echo(f"- {st['name']}: {'enabled' if st['enabled'] else 'disabled'} "
                   f"recipients={st['recipients']} token={st['token'] or 'none'}")


@integration.command("status")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def integration_status(config_path: str) -> None:
    """Show integration status as JSON (tokens masked)."""
    from wth_ai.integrations.manager import IntegrationManager
    click.echo(json.dumps(IntegrationManager(_load_config(config_path)).status(), indent=2))


@integration.command("test")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def integration_test(config_path: str) -> None:
    """Dry-run a synthetic notification to enabled adapters (no token leaked)."""
    from wth_ai.integrations.manager import IntegrationManager
    click.echo(json.dumps(IntegrationManager(_load_config(config_path)).test(dry_run=True), indent=2))


def _set_integration_enabled(config_path: str, platform: str, enabled: bool) -> None:
    config = _load_config(config_path)
    getattr(config.integrations, platform).enabled = enabled
    config.save_user_config()
    click.echo(f"{platform}: {'enabled' if enabled else 'disabled'}")


@integration.group()
def telegram() -> None:
    """Telegram integration management."""


@telegram.command("setup")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def telegram_setup(config_path: str) -> None:
    """Store the Telegram token in the vault and configure allowed chats."""
    from wth_ai.vault import SecretVault, vault_ref
    from wth_ai.workspace import init_workspace
    init_workspace()
    token = click.prompt("Telegram bot token", hide_input=True)
    SecretVault().set("telegram_bot_token", token)
    chats = click.prompt("Allowed chat IDs (comma-separated)", default="")
    config = _load_config(config_path)
    config.integrations.telegram.enabled = True
    config.integrations.telegram.bot_token_ref = vault_ref("telegram_bot_token")
    config.integrations.telegram.allowed_chat_ids = [c.strip() for c in chats.split(",") if c.strip()]
    config.save_user_config()
    click.echo("Telegram integration configured (token stored in vault).")


@telegram.command("test")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def telegram_test(config_path: str) -> None:
    """Dry-run a Telegram notification."""
    from wth_ai.integrations.manager import IntegrationManager
    from wth_ai.integrations.telegram import TelegramNotifier
    config = _load_config(config_path)
    mgr = IntegrationManager(config, adapters=[TelegramNotifier(config.integrations.telegram)])
    click.echo(json.dumps(mgr.test(dry_run=True), indent=2))


@telegram.command("enable")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def telegram_enable(config_path: str) -> None:
    """Enable the Telegram integration."""
    _set_integration_enabled(config_path, "telegram", True)


@telegram.command("disable")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def telegram_disable(config_path: str) -> None:
    """Disable the Telegram integration."""
    _set_integration_enabled(config_path, "telegram", False)


@integration.group()
def discord() -> None:
    """Discord integration management."""


@discord.command("setup")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def discord_setup(config_path: str) -> None:
    """Store the Discord token in the vault and configure guild/channels."""
    from wth_ai.vault import SecretVault, vault_ref
    from wth_ai.workspace import init_workspace
    init_workspace()
    token = click.prompt("Discord bot token", hide_input=True)
    SecretVault().set("discord_bot_token", token)
    guild = click.prompt("Guild ID", default="")
    channels = click.prompt("Allowed channel IDs (comma-separated)", default="")
    config = _load_config(config_path)
    config.integrations.discord.enabled = True
    config.integrations.discord.bot_token_ref = vault_ref("discord_bot_token")
    config.integrations.discord.guild_id = guild.strip() or None
    config.integrations.discord.allowed_channel_ids = [c.strip() for c in channels.split(",") if c.strip()]
    config.save_user_config()
    click.echo("Discord integration configured (token stored in vault).")


@discord.command("test")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def discord_test(config_path: str) -> None:
    """Dry-run a Discord notification."""
    from wth_ai.integrations.manager import IntegrationManager
    from wth_ai.integrations.discord import DiscordNotifier
    config = _load_config(config_path)
    mgr = IntegrationManager(config, adapters=[DiscordNotifier(config.integrations.discord)])
    click.echo(json.dumps(mgr.test(dry_run=True), indent=2))


@discord.command("enable")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def discord_enable(config_path: str) -> None:
    """Enable the Discord integration."""
    _set_integration_enabled(config_path, "discord", True)


@discord.command("disable")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def discord_disable(config_path: str) -> None:
    """Disable the Discord integration."""
    _set_integration_enabled(config_path, "discord", False)


@cli.group()
def approval() -> None:
    """Manage the minimal approval registry."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@approval.command("list")
def approval_list() -> None:
    """List approval requests."""
    from wth_ai.approvals import ApprovalRegistry
    rows = ApprovalRegistry().list_all()
    if not rows:
        click.echo("(no approvals)")
        return
    for r in rows:
        click.echo(f"- {r['approval_id']} [{r['status']}] job={r['job_id']} "
                   f"action={r['action']} risk={r['risk']}")


@approval.command("approve")
@click.argument("approval_id")
def approval_approve(approval_id: str) -> None:
    """Approve a pending request."""
    from wth_ai.approvals import ApprovalRegistry
    rec = ApprovalRegistry().approve(approval_id)
    click.echo(f"{approval_id} -> {rec['status']}" if rec else f"{approval_id}: not found")


@approval.command("deny")
@click.argument("approval_id")
def approval_deny(approval_id: str) -> None:
    """Deny a pending request."""
    from wth_ai.approvals import ApprovalRegistry
    rec = ApprovalRegistry().deny(approval_id)
    click.echo(f"{approval_id} -> {rec['status']}" if rec else f"{approval_id}: not found")


def _job_manager(config_path: str):
    from wth_ai.jobs.manager import JobManager
    from wth_ai.workspace import init_workspace
    init_workspace()
    return JobManager(_load_config(config_path))


@cli.group()
def job() -> None:
    """Create and manage jobs."""


@job.command("create")
@click.argument("job_type")
@click.option("--target", default=None)
@click.option("--profile", default=None)
@click.option("--priority", default="normal", type=click.Choice(["low", "normal", "high"]))
@click.option("--requires-approval", is_flag=True, help="Park the job for approval before running.")
@click.option("--mode", default=None,
              type=click.Choice(["passive-only", "safe-active", "authenticated-safe",
                                 "approval-required", "report-only", "recovery"]),
              help="Execution-mode envelope (default: derived from profile).")
@click.option("--objective", "objectives", multiple=True, help="Engagement objective (repeatable).")
@click.option("--budget", "request_budget", default=None, type=int, help="Max request budget.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_create(job_type: str, target, profile, priority: str, requires_approval: bool,
               mode: str | None, objectives, request_budget, config_path: str) -> None:
    """Create a JOB_TYPE job (recon|executor|report|provider_test|custom)."""
    mgr = _job_manager(config_path)
    j = mgr.create_job(
        job_type, target=target, profile=profile, priority=priority,
        requires_approval=requires_approval, created_by="cli",
        execution_mode=mode, objectives=list(objectives) or None,
        request_budget=request_budget,
    )
    if j.status == "failed":
        click.echo(f"Rejected: {j.error} (job {j.job_id})")
    else:
        meta = j.metadata or {}
        click.echo(
            f"Created {j.job_id} type={j.type} status={j.status} "
            f"mode={meta.get('execution_mode')} env={meta.get('environment')} "
            f"approval={meta.get('requires_approval', False)}"
        )


@job.command("list")
@click.option("--status", default=None)
@click.option("--type", "job_type", default=None)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_list(status, job_type, config_path: str) -> None:
    """List jobs (optionally filter by --status / --type)."""
    mgr = _job_manager(config_path)
    jobs = mgr.list_jobs(status=status, type=job_type)
    if not jobs:
        click.echo("(no jobs)")
        return
    for j in jobs:
        p = j.progress
        click.echo(f"- {j.job_id} [{j.status}] {j.type} target={j.target or '-'} "
                   f"priority={j.priority} progress={p['current']}/{p['total']}")


@job.command("show")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_show(job_id: str, config_path: str) -> None:
    """Show a job as JSON."""
    mgr = _job_manager(config_path)
    j = mgr.get_job(job_id)
    click.echo(json.dumps(j.to_dict(), indent=2, default=str) if j else f"{job_id}: not found")


@job.command("logs")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_logs(job_id: str, config_path: str) -> None:
    """Print a job's log."""
    mgr = _job_manager(config_path)
    text = mgr.logs(job_id)
    click.echo(text if text else f"(no logs for {job_id})")


@job.command("stop")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_stop(job_id: str, config_path: str) -> None:
    """Stop a job."""
    mgr = _job_manager(config_path)
    j = mgr.stop_job(job_id)
    click.echo(f"{job_id} -> {j.status}" if j else f"{job_id}: not found")


@job.command("retry")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_retry(job_id: str, config_path: str) -> None:
    """Re-queue a failed/stopped job."""
    mgr = _job_manager(config_path)
    j = mgr.retry_job(job_id)
    click.echo(f"{job_id} -> {j.status}" if j else f"{job_id}: not found")


@job.command("delete")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_delete(job_id: str, config_path: str) -> None:
    """Delete a job record."""
    mgr = _job_manager(config_path)
    click.echo(f"Deleted {job_id}" if mgr.delete_job(job_id) else f"{job_id}: not found")


@cli.group()
def worker() -> None:
    """Run and inspect the job worker."""


@worker.command("run")
@click.option("--no-drain", is_flag=True, help="Run as a daemon loop instead of draining once.")
@click.option("--max-jobs", default=None, type=int)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def worker_run(no_drain: bool, max_jobs, config_path: str) -> None:
    """Process queued jobs (drains by default; --no-drain loops as a daemon)."""
    from wth_ai.jobs.worker import WorkerEngine
    w = WorkerEngine(_job_manager(config_path))
    summary = w.run(drain=not no_drain, max_jobs=max_jobs)
    click.echo(json.dumps(summary))


@worker.command("run-once")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def worker_run_once(config_path: str) -> None:
    """Process a single job and report what happened."""
    from wth_ai.jobs.worker import WorkerEngine
    w = WorkerEngine(_job_manager(config_path))
    click.echo(json.dumps(w.run_once(), indent=2, default=str))


@worker.command("status")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def worker_status(config_path: str) -> None:
    """Show worker + job summary."""
    from wth_ai.jobs.worker import WorkerEngine
    w = WorkerEngine(_job_manager(config_path))
    click.echo(json.dumps(w.status(), indent=2, default=str))


@worker.command("stop")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def worker_stop(config_path: str) -> None:
    """Mark the worker stopped (clears the run flag)."""
    from wth_ai.jobs.worker import WorkerEngine
    WorkerEngine(_job_manager(config_path)).stop()
    click.echo("worker stopped")


def _recon_registry():
    from wth_ai.recon.registry import ReconToolRegistry
    from wth_ai.workspace import init_workspace
    init_workspace()
    return ReconToolRegistry()


def _run_recon_job(
    config_path: str, target: str, profile: str, *,
    realtime: bool = False, mode: str | None = None,
) -> None:
    """Create a recon job and drain the worker (never bypasses the job system)."""
    from wth_ai.jobs.worker import WorkerEngine
    mgr = _job_manager(config_path)
    j = mgr.create_job(
        "recon", target=target, profile=profile, created_by="cli",
        execution_mode=mode,
        metadata={"authorized": True, "realtime": realtime, "profile": profile},
    )
    if j.status == "failed":
        click.echo(f"Rejected: {j.error} (job {j.job_id})")
        return
    eff_mode = (j.metadata or {}).get("execution_mode", "safe-active")
    eff_env = (j.metadata or {}).get("environment", "unknown")
    click.echo(
        f"Created {j.job_id} (recon {target}, profile={profile}, "
        f"mode={eff_mode}, env={eff_env}) — running..."
    )
    WorkerEngine(mgr).run(drain=True)
    done = mgr.get_job(j.job_id)
    if not done:
        click.echo(f"{j.job_id}: not found after run")
        return
    res = done.result or {}
    click.echo(f"Job {j.job_id} -> {done.status}")
    if res:
        click.echo(f"  output: {res.get('output_dir', '-')}")
        click.echo(f"  assets: {json.dumps(res.get('summary', {}).get('assets', {}))}")
        click.echo(f"  priority targets: {res.get('priority_targets', 0)}")


def _run_exec_job(config_path: str, target: str, *, mode: str, profile: str,
                  recon_job_id: str | None = None) -> None:
    """Create a gated, non-destructive executor job and drain the worker."""
    from wth_ai.jobs.worker import WorkerEngine
    mgr = _job_manager(config_path)
    meta = {"authorized": True, "profile": profile}
    if recon_job_id:
        meta["recon_job_id"] = recon_job_id
    j = mgr.create_job("executor", target=target, profile=profile, created_by="cli",
                       execution_mode=mode, metadata=meta)
    if j.status == "failed":
        click.echo(f"Rejected: {j.error} (job {j.job_id})")
        return
    eff_mode = (j.metadata or {}).get("execution_mode", mode)
    approval = (j.metadata or {}).get("requires_approval", False)
    click.echo(f"Created {j.job_id} (executor {target}, mode={eff_mode}, "
               f"approval={approval}) — running...")
    WorkerEngine(mgr).run(drain=True)
    done = mgr.get_job(j.job_id)
    if not done:
        click.echo(f"{j.job_id}: not found after run")
        return
    res = done.result or {}
    click.echo(f"Job {j.job_id} -> {done.status}")
    if done.status == "waiting_approval":
        click.echo(f"  approval required: approve with `wth approval approve <id>` then `wth worker once`")
    if res:
        click.echo(f"  output: {res.get('output_dir', '-')}")
        click.echo(f"  stats: {json.dumps(res.get('stats', {}))}")
        click.echo(f"  findings: {res.get('findings', 0)}")


@cli.group("exec")
def exec_group() -> None:
    """Active (non-destructive, gated) execution engine (Fase 6)."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@exec_group.command("run")
@click.argument("target")
@click.option("--mode", default="safe-active",
              type=click.Choice(["safe-active", "authenticated-safe", "approval-required"]),
              help="Execution-mode envelope for active testing.")
@click.option("--profile", default="recon-only")
@click.option("--recon-job", "recon_job_id", default=None, help="Reuse a prior recon job's results.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def exec_run(target: str, mode: str, profile: str, recon_job_id: str | None, config_path: str) -> None:
    """Run gated, non-destructive active testing on TARGET (via the job system)."""
    _run_exec_job(config_path, target, mode=mode, profile=profile, recon_job_id=recon_job_id)


@cli.group()
def recon() -> None:
    """Real-time recon platform (Fase 5): plan, run, watch, diff, tools."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@recon.command("run")
@click.argument("target")
@click.option("--profile", default="recon-only",
              type=click.Choice(["budget", "recon-only", "default", "high-quality", "local"]))
@click.option("--mode", default=None,
              type=click.Choice(["passive-only", "safe-active", "authenticated-safe",
                                 "approval-required", "report-only", "recovery"]),
              help="Execution-mode envelope (default: derived from profile).")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_run(target: str, profile: str, mode: str | None, config_path: str) -> None:
    """Run authorized, non-destructive recon on TARGET (via the job system)."""
    _run_recon_job(config_path, target, profile, mode=mode)


@recon.command("quick")
@click.argument("target")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_quick(target: str, config_path: str) -> None:
    """Fast recon (recon-only profile) on TARGET."""
    _run_recon_job(config_path, target, "recon-only")


@recon.command("watch")
@click.argument("target")
@click.option("--interval", default="6h", help="Polling interval (>=1h), e.g. 6h, 90m, 1d.")
@click.option("--profile", default="recon-only")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_watch(target: str, interval: str, profile: str, config_path: str) -> None:
    """Register a watch (interval >=1h, jittered) and run one recon pass now."""
    from wth_ai.recon.realtime import WatchConfig, parse_interval, save_watch
    seconds = parse_interval(interval)
    save_watch(WatchConfig(target=target, interval_seconds=seconds, profile=profile))
    click.echo(f"watch registered: {target} every {seconds}s (profile={profile})")
    _run_recon_job(config_path, target, profile, realtime=True)


@recon.command("plan")
@click.argument("target")
@click.option("--profile", default="recon-only",
              type=click.Choice(["budget", "recon-only", "default", "high-quality", "local"]))
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_plan(target: str, profile: str, config_path: str) -> None:
    """Show the safe recon plan for TARGET (no execution)."""
    from wth_ai.recon.planner import ReconPlanner
    p = ReconPlanner(_recon_registry()).plan(target, profile=profile)
    click.echo(f"# Recon plan — {target} (type={p.target_type}, profile={profile})")
    click.echo(f"Selected: {len(p.selected)} tools | Skipped: {len(p.skipped)}")
    for t in p.selected:
        click.echo(f"  + {t.tool} [{t.category}/{t.execution_mode}] {t.reason}")
    click.echo("  ...")
    for t in p.skipped[:8]:
        click.echo(f"  - {t.tool}: {t.reason}")


@recon.command("show")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_show(job_id: str, config_path: str) -> None:
    """Show a recon job's summary JSON."""
    from wth_ai.workspace import wth_home
    path = wth_home() / "wth-results" / job_id / "recon_summary.json"
    click.echo(path.read_text(encoding="utf-8") if path.exists() else f"{job_id}: no summary")


@recon.command("report")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_report(job_id: str, config_path: str) -> None:
    """Print a recon job's markdown report."""
    from wth_ai.workspace import wth_home
    path = wth_home() / "wth-results" / job_id / "recon_report.md"
    click.echo(path.read_text(encoding="utf-8") if path.exists() else f"{job_id}: no report")


@recon.command("diff")
@click.argument("old_job_id")
@click.argument("new_job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_diff(old_job_id: str, new_job_id: str, config_path: str) -> None:
    """Diff two recon jobs' normalized results."""
    from wth_ai.recon.diff import diff_results
    from wth_ai.workspace import wth_home
    base = wth_home() / "wth-results"

    def _load(jid: str):
        p = base / jid / "normalized_result.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    changes = diff_results(_load(old_job_id), _load(new_job_id))
    click.echo(json.dumps(changes, indent=2))


@recon.command("changes")
@click.argument("target")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_changes(target: str, config_path: str) -> None:
    """Show watch status + diff between the last two recon runs for TARGET."""
    from wth_ai.recon.diff import diff_results
    from wth_ai.recon.realtime import load_watch
    from wth_ai.workspace import wth_home
    mgr = _job_manager(config_path)
    jobs = [j for j in mgr.list_jobs(type="recon", status="completed") if (j.target or "") == target]
    cfg = load_watch(target)
    if cfg:
        click.echo(f"watch: interval={cfg.interval_seconds}s last_job={cfg.last_job_id}")
    if len(jobs) < 2:
        click.echo("need >=2 completed recon runs to show changes")
        return
    base = wth_home() / "wth-results"

    def _load(jid: str):
        p = base / jid / "normalized_result.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    new, old = jobs[0], jobs[1]
    click.echo(json.dumps(diff_results(_load(old.job_id), _load(new.job_id)), indent=2))


@recon.group("tools")
def recon_tools() -> None:
    """Inspect the 200+ recon tool registry."""


@recon_tools.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_list(config_path: str) -> None:
    """List all recon tools grouped by category."""
    click.echo(_recon_registry().format_list())


@recon_tools.command("available")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_available(config_path: str) -> None:
    """List tools available in this environment (builtin/installed/api-keyed)."""
    reg = _recon_registry()
    for s in sorted(reg.available(), key=lambda x: x.name):
        click.echo(f"- {s.name} [{s.execution_mode}] category={s.category}")


@recon_tools.command("missing")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_missing(config_path: str) -> None:
    """List external/api tools that are not available (with install hints)."""
    reg = _recon_registry()
    for s in sorted(reg.missing(), key=lambda x: x.name):
        click.echo(f"- {s.name} [{s.execution_mode}] {reg.install_hint(s.name)}")


@recon_tools.command("doctor")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_doctor(config_path: str) -> None:
    """Recon registry health summary."""
    click.echo(json.dumps(_recon_registry().summary(), indent=2))


@recon_tools.command("show")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_show(name: str, config_path: str) -> None:
    """Show a tool spec."""
    reg = _recon_registry()
    spec = reg.get(name)
    if not spec:
        click.echo(f"{name}: not in catalog")
        return
    d = spec.to_dict()
    d["available"] = reg.is_available(name)
    d["enabled"] = reg.is_enabled(name)
    click.echo(json.dumps(d, indent=2))


@recon_tools.command("enable")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_enable(name: str, config_path: str) -> None:
    """Enable a tool (persisted override)."""
    click.echo(f"enabled {name}" if _recon_registry().enable(name) else f"{name}: not in catalog")


@recon_tools.command("disable")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_disable(name: str, config_path: str) -> None:
    """Disable a tool (persisted override)."""
    click.echo(f"disabled {name}" if _recon_registry().disable(name) else f"{name}: not in catalog")


@cli.group()
def bot() -> None:
    """Discord / Telegram bot daemons."""


@bot.command("discord")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def bot_discord(config_path: str) -> None:
    """Start Discord bot daemon."""
    from wth_ai.bots.discord_bot import run_discord_bot
    run_discord_bot(_load_config(config_path))


@bot.command("telegram")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def bot_telegram(config_path: str) -> None:
    """Start Telegram bot daemon."""
    from wth_ai.bots.telegram_bot import run_telegram_bot
    run_telegram_bot(_load_config(config_path))


@bot.command("status")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def bot_status(config_path: str) -> None:
    """Check bot configuration status."""
    cfg = _load_config(config_path)
    lines = [
        "## Bot Status",
        f"- Discord token: {'set' if cfg.bots.discord_token else 'not set'}",
        f"- Discord guilds: {cfg.bots.discord_guild_ids or 'all'}",
        f"- Telegram token: {'set' if cfg.bots.telegram_token else 'not set'}",
        f"- Telegram chats: {cfg.bots.telegram_chat_ids or 'all'}",
    ]
    click.echo("\n".join(lines))


@cli.command()
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def interactive(config_path: str) -> None:
    """Start interactive REPL."""
    from wth_ai.cli.setup_wizard import ensure_setup
    ensure_setup()
    _orch(config_path).run_interactive()


@cli.command("run")
@click.argument("command")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
@click.option("--fresh", is_flag=True, help="Start new session instead of resuming latest.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON wrapper.")
def run_command(command: str, config_path: str, fresh: bool, as_json: bool) -> None:
    """Run a single slash command or chat message."""
    orch = _orch(config_path, fresh=fresh)
    result = orch.run_once(command)
    if as_json:
        click.echo(json.dumps({"command": command, "response": result}, ensure_ascii=False))
    else:
        click.echo(result)


@cli.command("new-session")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def new_session_cmd(config_path: str) -> None:
    """Start a fresh session."""
    sid = _orch(config_path, fresh=True).new_session()
    click.echo(f"New session: {sid}")


@cli.command()
@click.argument("session_id", required=False)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def resume(session_id: str | None, config_path: str) -> None:
    """Resume a saved session."""
    click.echo(_orch(config_path).resume(session_id))


@cli.command()
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def sessions(config_path: str) -> None:
    """List saved sessions."""
    click.echo(_orch(config_path).run_once("/sessions"))


@cli.command()
@click.option("--target", "-t", required=True, help="Target domain/URL/repo path.")
@click.option("--scope", multiple=True, help="In-scope patterns.")
@click.option("--env", default="unknown", help="Environment: local/staging/production/CTF.")
@click.option("--permission", "-p", default="read_only", help="Permission mode.")
@click.option("--authorized/--no-authorized", default=False, help="Confirm authorization.")
@click.option("--plan", is_flag=True, help="Auto-run /plan after setup.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def assess(
    target: str,
    scope: tuple[str, ...],
    env: str,
    permission: str,
    authorized: bool,
    plan: bool,
    config_path: str,
) -> None:
    """Quick-start assessment with scope + permission preset."""
    from wth_ai.cli.setup_wizard import ensure_setup
    ensure_setup()
    orch = _orch(config_path, fresh=True)
    in_scope = list(scope) or [target]
    orch.run_once(
        f"/scope set target={target} environment={env} authorized={'yes' if authorized else 'no'} "
        f"in_scope={','.join(in_scope)}"
    )
    orch.run_once(f"/permission {permission}")
    console = Console()
    console.print(orch.startup_message())
    if plan:
        console.print(orch.run_once(f"/plan Audit {target} — environment {env}"))
    else:
        console.print("Scope set. Lanjut: wth-ai interactive atau wth-ai run '/recon'")


if __name__ == "__main__":
    cli()
