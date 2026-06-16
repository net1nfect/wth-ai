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
