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
@click.option("--local", is_flag=True, help="Embedded mode (skip gateway client).")
@click.option("--plain", is_flag=True, help="Plain unicode terminal REPL (default).")
@click.option("--tui", is_flag=True, help="Fullscreen Textual TUI (requires wth-ai[tui]).")
@click.pass_context
def cli(ctx: click.Context, config_path: str, version: bool, skip_setup: bool, local: bool, plain: bool, tui: bool) -> None:
    """WTH-AI v1.0 — realtime bug hunter with dual-agent AI."""
    if version:
        click.echo(f"WTH-AI v{__version__}")
        return
    if skip_setup:
        os.environ["WTH_SKIP_SETUP"] = "1"
    if local:
        os.environ["WTH_LOCAL"] = "1"
    if tui:
        os.environ["WTH_TUI"] = "1"
        os.environ.pop("WTH_PLAIN", None)
    elif plain:
        os.environ["WTH_PLAIN"] = "1"
    if ctx.invoked_subcommand is None:
        from wth_ai.cli.setup_wizard import ensure_setup
        ensure_setup()
        _orch(config_path).run_interactive(local=local)


@cli.command()
@click.option("--reset", is_flag=True, help="Reset existing configuration.")
@click.option("--reconfigure", is_flag=True, help="Re-run wizard without deleting config/vault.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def setup(config_path: str, reset: bool, reconfigure: bool) -> None:
    """Run first-time setup wizard (API keys, models, bots)."""
    from wth_ai.cli.setup_wizard import run_setup_wizard
    run_setup_wizard(reset=reset and not reconfigure)


@cli.command()
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def doctor(config_path: str) -> None:
    """Diagnose workspace, vault, config, and tool health."""
    from wth_ai.tools.doctor import ToolDoctor
    click.echo(ToolDoctor(_load_config(config_path)).full_report())


@cli.command("usage")
def usage_cmd() -> None:
    """Show recorded token/cost usage for this machine."""
    from wth_ai.cli.ui.tables import plain_output, render_table
    from wth_ai.llm.usage import get_ledger

    ledger = get_ledger()
    data = ledger.summary()
    if not data["calls"] or plain_output():
        click.echo(ledger.format_summary())
        return

    cost_note = f"${data['cost_usd']}" if data["cost_usd"] else "tidak dilaporkan provider"
    # Totals live in the CAPTION, not the title: rich wraps a title to the TABLE
    # width (not the console width), so a long title broke into two lines on a
    # narrow table and the output read as damaged.
    caption = (
        f"{data['calls']} call · {data['total_tokens']} token · biaya: {cost_note}"
    )
    tail = ledger.budget_line()
    if tail:
        caption += "\n" + tail

    by_model = [[model, slot["calls"], slot["tokens"],
                 f"${slot['cost_usd']}" if slot["cost_usd"] else "—"]
                for model, slot in sorted(data["by_model"].items(), key=lambda kv: -kv[1]["tokens"])]
    click.echo(render_table(
        "Pemakaian per model", ["model", "call", "token", "biaya"], by_model, caption=caption))

    if data["by_role"]:
        by_role = [[role, slot["calls"], slot["tokens"]]
                   for role, slot in sorted(data["by_role"].items(), key=lambda kv: -kv[1]["tokens"])]
        click.echo()
        click.echo(render_table("Per role", ["role", "call", "token"], by_role))


@cli.group("autopilot", invoke_without_command=True)
@click.option("--target", default=None, help="Target (default: dari scope tersimpan).")
@click.option("--steps", default=6, show_default=True, help="Langkah per cycle.")
@click.option("--cycles", default=2, show_default=True, help="Jumlah pass adaptif.")
@click.option("--dry-run", is_flag=True, help="Susun rencana tanpa menjalankan apa pun.")
def autopilot_group(target: str | None, steps: int, cycles: int, dry_run: bool) -> None:
    """Operasi otonom: susun langkah berbatas dari temuan, jalankan, lalu belajar."""
    from wth_ai.workspace import init_workspace

    init_workspace()
    from wth_ai.config import WTHConfig
    from wth_ai.orchestrator import WTHOrchestrator

    orch = WTHOrchestrator(WTHConfig.load(), fresh=True)
    chosen = target or orch.state.scope.target
    if not chosen:
        click.echo("Tidak ada target. Pakai --target atau set scope dulu (/target).")
        return
    if dry_run:
        from wth_ai.hunt.autopilot import run_autopilot

        report = run_autopilot(target=chosen, run_probe=lambda probe: None,
                               state=orch.state, max_cycles=cycles,
                               max_steps_per_cycle=steps, dry_run=True)
        click.echo(f"DRY-RUN: {len(report.steps)} langkah disusun, tidak ada yang dijalankan")
        return
    click.echo(orch.handle_input(f"/autopilot {steps}"))


@cli.group("payloads")
def payloads_group() -> None:
    """Pustaka payload (berbatas) untuk uji destruktif yang terkendali."""


@payloads_group.command("list")
@click.option("--class", "vuln_class", default=None, help="Filter kelas kerentanan.")
@click.option("--max-blast", default="single-object", show_default=True,
              help="self | single-object | mass")
@click.option("--include-destructive", is_flag=True, help="Ikut sertakan yang butuh opt-in destruktif.")
def payloads_list(vuln_class: str | None, max_blast: str, include_destructive: bool) -> None:
    """Daftar payload yang boleh dipakai menurut batas yang diberikan."""
    from wth_ai.hunt.payloads import filter_payloads, format_payloads

    items = filter_payloads(vuln_class, max_blast=max_blast, include_destructive=include_destructive)
    click.echo(format_payloads(items) + f"\n\n({len(items)} payload ditampilkan)")


@payloads_group.command("show")
@click.argument("payload_id")
def payloads_show(payload_id: str) -> None:
    """Detail satu payload, termasuk pemulihan dan catatannya."""
    import json

    from wth_ai.hunt.payloads import get_payload

    payload = get_payload(payload_id)
    if payload is None:
        click.echo(f"{payload_id}: tidak ada di katalog")
        return
    click.echo(json.dumps(payload.to_dict(), indent=2, ensure_ascii=False))


@payloads_group.command("doc")
@click.option("--out", default="docs/attack/destructive-library.md", show_default=True)
def payloads_doc(out: str) -> None:
    """Tulis ulang dokumentasi .md dari katalog (sumber tunggal: kode)."""
    from pathlib import Path as _Path

    from wth_ai.hunt.payloads import PAYLOADS, to_markdown

    path = _Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(), encoding="utf-8")
    click.echo(f"ditulis: {path} ({len(PAYLOADS)} payload)")


@cli.group(name="eval")
def eval_group() -> None:
    """Measure the self-improvement loop (offline: no LLM, no network)."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@eval_group.command("run")
def eval_run() -> None:
    """Collect one measurement report and compare it with the previous run."""
    from wth_ai.eval import compare, format_report, load_latest, run
    report, path = run()
    previous = load_latest(exclude=path.name)
    click.echo(format_report(report, comparison=compare(report, previous)))
    click.echo(f"\nLaporan disimpan: {path}")


@eval_group.command("act")
def eval_act() -> None:
    """Jalankan aksi self-improvement dari hasil pengukuran (bobot + backfill)."""
    from wth_ai.eval import act as act_mod
    from wth_ai.findings_db import FindingsDB
    from wth_ai.memory.learning import HuntMemoryLearning

    actions = act_mod.act_on_report(None, learning=HuntMemoryLearning(), findings_db=FindingsDB())
    click.echo(act_mod.format_actions(actions))


@eval_group.command("history")
def eval_history() -> None:
    """List stored reports, newest last."""
    from wth_ai.eval import eval_dir
    directory = eval_dir()
    files = sorted(directory.glob("eval-*.json")) if directory.exists() else []
    if not files:
        click.echo("Belum ada laporan eval.")
        return
    from wth_ai.cli.ui.tables import render_table

    rows = []
    for path in files[-10:]:
        stat = path.stat()
        from datetime import datetime

        stamp = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        rows.append([path.name, stat.st_size, stamp])
    click.echo(render_table("Laporan eval (10 terbaru)", ["file", "byte", "waktu"], rows))


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
    "9router": "router9_api_key",
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
    elif name == "9router":
        key = click.prompt("9router API key", hide_input=True, default="")
        if key:
            SecretVault().set(_PROVIDER_VAULT_KEY[name], key)
        url = click.prompt("9router base URL", default=config.secrets.router9_base_url)
        config.secrets.router9_base_url = url
        config.secrets.router9_api_key = key or config.secrets.router9_api_key
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


@provider.group("keys")
def provider_keys() -> None:
    """Manage provider API keys."""


@provider_keys.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_keys_list(config_path: str) -> None:
    from wth_ai.config import PROVIDER_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    from wth_ai.vault import mask_secret
    router = LLMProviderRouter(_load_config(config_path))
    for name in PROVIDER_NAMES:
        key = router.provider_key(name)
        status = "local" if name == "ollama" else ("configured" if key else "missing")
        if key and name != "ollama":
            status = mask_secret(key)
        click.echo(f"- {name}: {status}")


@provider_keys.command("set")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_keys_set(name: str, config_path: str) -> None:
    provider_add(name, config_path)


@provider_keys.command("test")
@click.argument("name", required=False, default="--all")
@click.option("--model", default=None)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_keys_test(name: str, model: str | None, config_path: str) -> None:
    from wth_ai.config import PROVIDER_NAMES
    from wth_ai.llm.router import LLMProviderRouter
    router = LLMProviderRouter(_load_config(config_path))
    targets = list(PROVIDER_NAMES) if name in ("--all", "all") else [name]
    for p in targets:
        result = router.test_provider(p, model)
        click.echo(json.dumps(result, indent=2, default=str))


@provider.command("failover")
@click.argument("action", type=click.Choice(["enable", "disable", "status"]))
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def provider_failover(action: str, config_path: str) -> None:
    config = _load_config(config_path)
    if action == "enable":
        config.provider_prefs.failover_enabled = True
    elif action == "disable":
        config.provider_prefs.failover_enabled = False
    config.save_user_config()
    click.echo(f"failover: {'enabled' if config.provider_prefs.failover_enabled else 'disabled'}")
    click.echo(f"order: {config.provider_prefs.failover_order}")


@cli.group()
def safety() -> None:
    """Safety / unrestricted exploration mode."""


@safety.command("unrestricted")
@click.argument("state", type=click.Choice(["on", "off", "status"]))
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def safety_unrestricted(state: str, config_path: str) -> None:
    from wth_ai.engagement.mode import is_unrestricted, set_unrestricted
    config = _load_config(config_path)
    if state == "status":
        click.echo(f"unrestricted: {is_unrestricted(config)}")
        return
    enabled = state == "on"
    set_unrestricted(enabled)
    config.safety.unrestricted = enabled
    config.save_user_config()
    click.echo(f"unrestricted mode: {'ON' if enabled else 'OFF'}")


@cli.group()
def engage() -> None:
    """Start CTF or bug bounty engagement."""


@engage.command("start")
@click.option("--mode", type=click.Choice(["ctf", "bug-bounty"]), required=True)
@click.option("--target", "-t", required=True)
@click.option("--authorized/--no-authorized", default=True)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def engage_start(mode: str, target: str, authorized: bool, config_path: str) -> None:
    from wth_ai.engagement.mode import EngagementMode, set_engagement_mode, set_unrestricted
    from wth_ai.profiles import apply_profile
    profile = "ctf-aggressive" if mode == "ctf" else "bug-bounty"
    apply_profile(profile)
    set_engagement_mode(EngagementMode.CTF if mode == "ctf" else EngagementMode.BUG_BOUNTY)
    set_unrestricted(True)
    orch = _orch(config_path, fresh=True)
    orch.run_once(
        f"/scope set target={target} authorized={'yes' if authorized else 'no'} environment={mode}"
    )
    orch.run_once("/permission unrestricted")
    click.echo(f"Engagement started: mode={mode} target={target} profile={profile}")


@cli.group()
def mcp() -> None:
    """MCP server integration."""


@mcp.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def mcp_list(config_path: str) -> None:
    from wth_ai.mcp.manager import MCPClientManager
    mgr = MCPClientManager(_load_config(config_path))
    click.echo(mgr.format_status())


@mcp.command("status")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def mcp_status(config_path: str) -> None:
    mcp_list(config_path)


@mcp.command("tools")
@click.option("--server", default=None)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def mcp_tools(server: str | None, config_path: str) -> None:
    from wth_ai.mcp.manager import MCPClientManager
    mgr = MCPClientManager(_load_config(config_path))
    mgr.connect_all()
    for sname, tools in mgr.list_tools(server).items():
        click.echo(f"## {sname}")
        for t in tools:
            click.echo(f"  - {t.get('name', t)}")


@mcp.command("test")
@click.argument("server")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def mcp_test(server: str, config_path: str) -> None:
    from wth_ai.mcp.manager import MCPClientManager
    mgr = MCPClientManager(_load_config(config_path))
    status = mgr.connect_all()
    click.echo(json.dumps(status, indent=2))


@mcp.command("call")
@click.argument("server")
@click.argument("tool")
@click.option("--args", "args_json", default="{}")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def mcp_call(server: str, tool: str, args_json: str, config_path: str) -> None:
    from wth_ai.mcp.manager import MCPClientManager
    mgr = MCPClientManager(_load_config(config_path))
    mgr.connect_all()
    args = json.loads(args_json)
    result = mgr.call_tool(server, tool, args)
    click.echo(json.dumps({"success": result.success, "output": result.output, "error": result.error}, indent=2))


@mcp.command("serve")
@click.option("--stdio", is_flag=True, help="Serve read-only tools on stdio (JSON lines).")
def mcp_serve(stdio: bool) -> None:
    """Expose read-only WTH tools (graph_export, recon_plan, findings_list)."""
    from wth_ai.mcp.serve import McpServeRegistry
    reg = McpServeRegistry()
    if stdio:
        reg.serve_stdio()
        return
    for t in reg.list_tools():
        click.echo(f"- {t['name']} (mutating={t['mutating']})")


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
    from wth_ai.cli.ui.tables import render_table

    rows = []
    for role in ROLE_NAMES:
        rc = router.role_config(role)
        rows.append([role, rc.type, rc.model, getattr(rc, "backup_model", "") or "—"])
    click.echo(render_table("Model per role", ["role", "provider", "model", "backup"], rows))


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
    from wth_ai.integrations.app import build_integration_runtime
    from wth_ai.jobs.manager import JobManager
    from wth_ai.workspace import init_workspace
    init_workspace()
    config = _load_config(config_path)
    build_integration_runtime(config)
    return JobManager(config)


@cli.group()
def job() -> None:
    """Create and manage jobs."""


@job.command("create")
@click.argument("job_type")
@click.option("--target", default=None)
@click.option("--profile", default=None)
@click.option("--priority", default="normal", type=click.Choice(["low", "normal", "high"]))
@click.option("--requires-approval", is_flag=True, help="Park the job for approval before running.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def job_create(job_type: str, target, profile, priority: str, requires_approval: bool, config_path: str) -> None:
    """Create a JOB_TYPE job (recon|executor|report|provider_test|custom)."""
    mgr = _job_manager(config_path)
    # `wth job create` is the operator naming the target directly, so the
    # authorization assertion belongs here at the CLI boundary: the recon
    # handler is now fail-closed (`metadata.authorized`, default False). Left
    # unset for every other job type so their own fail-closed defaults stay in
    # force -- notably `run_executor_job()`, which must not be handed a
    # pre-authorized flag it never asked for.
    meta = {"authorized": True, "profile": profile} if job_type == "recon" else None
    j = mgr.create_job(
        job_type, target=target, profile=profile, priority=priority,
        requires_approval=requires_approval, created_by="cli",
        metadata=meta,
    )
    if j.status == "failed":
        click.echo(f"Rejected: {j.error} (job {j.job_id})")
    else:
        click.echo(f"Created {j.job_id} type={j.type} status={j.status}")


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
def cron() -> None:
    """Manage scheduled recon/cron jobs."""
    from wth_ai.workspace import init_workspace
    init_workspace()


@cron.command("list")
def cron_list() -> None:
    """List cron entries."""
    from wth_ai.cron.store import CronStore

    entries = CronStore().list()
    if not entries:
        click.echo("(no cron entries)")
        return
    for e in entries:
        flag = "on" if e.enabled else "off"
        click.echo(
            f"- {e.cron_id} [{flag}] {e.job_type} {e.target} "
            f"schedule={e.schedule} profile={e.profile} next={e.next_run_at or '-'}"
        )


@cron.command("add")
@click.argument("target")
@click.option("--schedule", default="every 6h", show_default=True)
@click.option("--job-type", "job_type", default="recon", show_default=True)
@click.option("--profile", default="recon-only", show_default=True)
def cron_add(target: str, schedule: str, job_type: str, profile: str) -> None:
    """Add a cron entry."""
    from wth_ai.cron.store import CronStore

    entry = CronStore().add(job_type, target, schedule, profile=profile)
    click.echo(f"Added cron {entry.cron_id}: {job_type} {target} ({schedule})")


@cron.command("remove")
@click.argument("cron_id")
def cron_remove(cron_id: str) -> None:
    """Remove a cron entry."""
    from wth_ai.cron.store import CronStore

    click.echo(f"Removed {cron_id}" if CronStore().remove(cron_id) else f"{cron_id}: not found")


@cron.command("enable")
@click.argument("cron_id")
def cron_enable(cron_id: str) -> None:
    """Enable a cron entry."""
    from wth_ai.cron.store import CronStore

    click.echo(f"Enabled {cron_id}" if CronStore().set_enabled(cron_id, True) else f"{cron_id}: not found")


@cron.command("disable")
@click.argument("cron_id")
def cron_disable(cron_id: str) -> None:
    """Disable a cron entry."""
    from wth_ai.cron.store import CronStore

    click.echo(f"Disabled {cron_id}" if CronStore().set_enabled(cron_id, False) else f"{cron_id}: not found")


@cron.command("migrate-watch")
def cron_migrate_watch() -> None:
    """Import legacy recon watch configs into cron store."""
    from wth_ai.cron.store import CronStore

    n = CronStore().migrate_from_watch()
    click.echo(f"Migrated {n} watch config(s)")


@cli.group()
def gateway() -> None:
    """Hermes-style gateway daemon (sessions, worker, RPC)."""


@gateway.command("run")
@click.option("--foreground/--background", default=True, help="Run in foreground (default) or daemonize.")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def gateway_run(foreground: bool, config_path: str) -> None:
    """Start the WTH gateway daemon."""
    from wth_ai.gateway.background import is_gateway_running, read_gateway_pid, spawn_background
    from wth_ai.gateway.daemon import GatewayDaemon
    from wth_ai.workspace import init_workspace

    init_workspace()
    if not foreground:
        if is_gateway_running():
            pid = read_gateway_pid()
            click.echo(f"gateway already running (pid {pid})")
            return
        try:
            pid = spawn_background(config_path)
        except RuntimeError as exc:
            click.echo(f"error: {exc}", err=True)
            raise SystemExit(1) from exc
        click.echo(f"gateway started in background (pid {pid})")
        click.echo(f"  socket: ~/.wth-ai/runtime/gateway.sock")
        click.echo(f"  logs:   ~/.wth-ai/logs/gateway.log")
        click.echo("  stop:   wth gateway stop")
        return
    if is_gateway_running():
        pid = read_gateway_pid()
        click.echo(f"gateway already running (pid {pid}) — use `wth gateway stop` first")
        raise SystemExit(1)
    GatewayDaemon(_load_config(config_path)).start(foreground=True)


@gateway.command("stop")
def gateway_stop_cmd() -> None:
    """Stop the background gateway daemon."""
    from wth_ai.gateway.background import is_gateway_running, stop_gateway

    if not is_gateway_running():
        click.echo("gateway: not running")
        return
    if stop_gateway():
        click.echo("gateway stopped")
    else:
        click.echo("gateway: could not stop (no pid file)")
        raise SystemExit(1)


@gateway.command("status")
def gateway_status_cmd() -> None:
    """Show gateway health, pid, socket, and RPC summary."""
    from wth_ai.gateway.background import gateway_status_info

    info = gateway_status_info()
    if not info.get("running"):
        click.echo("gateway: not running")
        click.echo(f"  start: wth gateway run --background")
        return
    click.echo(json.dumps(info, indent=2, default=str))


@cli.group()
def worker() -> None:
    """Run and inspect the job worker (deprecated — prefer `wth gateway run`)."""


@worker.command("run")
@click.option("--no-drain", is_flag=True, help="Run as a daemon loop instead of draining once.")
@click.option("--max-jobs", default=None, type=int)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def worker_run(no_drain: bool, max_jobs, config_path: str) -> None:
    """Process queued jobs (drains by default; --no-drain loops as a daemon)."""
    click.echo("Note: prefer `wth gateway run` — worker is embedded in the gateway.", err=True)
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


def _registry_table(reg, specs, *, title: str, include_hint: bool = False) -> str:
    """Shared table for the tool-registry commands.

    Columns answer the operator's actual questions: is it usable here, where does
    it sit in the catalog, does it run or merely get listed, and is it risky.
    """
    from wth_ai.cli.ui.tables import render_table

    rows = []
    for s in sorted(specs, key=lambda x: (x.category, x.name)):
        status = "available" if reg.is_available(s.name) else "missing"
        enabled = "on" if reg.is_enabled(s.name) else "off"
        row = [s.name, status, s.category, s.execution_mode, enabled, s.risk_level]
        if include_hint:
            row.append(reg.install_hint(s.name) if status == "missing" else "")
        rows.append(row)
    columns = ["tool", "status", "category", "mode", "enabled", "risk"]
    if include_hint:
        columns.append("install_hint")
    available = sum(1 for s in specs if reg.is_available(s.name))
    caption = (
        f"{len(rows)} tools · {available} available · {len(rows) - available} missing"
    )
    return render_table(title, columns, rows, caption=caption)


def _recon_registry():
    from wth_ai.recon.registry import ReconToolRegistry
    from wth_ai.workspace import init_workspace
    init_workspace()
    return ReconToolRegistry()


def _run_recon_job(
    config_path: str,
    target: str,
    profile: str,
    *,
    realtime: bool = False,
    sync_graph: bool = True,
) -> None:
    """Create a recon job and drain the worker (never bypasses the job system)."""
    from wth_ai.cli.recon_commands import format_job_summary, run_recon_job, sync_graph_after_recon

    config = _load_config(config_path)
    mgr = _job_manager(config_path)
    session_id = None
    try:
        from wth_ai.session_store import SessionStore

        latest = SessionStore(config).load_latest()
        session_id = latest.session_id if latest else None
    except Exception:
        pass
    click.echo(f"Created recon job for {target} (profile={profile}) — running...")
    j, done = run_recon_job(
        mgr, target, profile, realtime=realtime, session_id=session_id,
    )
    if j.status == "failed":
        click.echo(f"Rejected: {j.error} (job {j.job_id})")
        return
    graph_nodes = 0
    if sync_graph and done and done.status == "completed":
        output_dir = str((done.result or {}).get("output_dir", ""))
        graph_nodes = sync_graph_after_recon(config, target, output_dir)
    click.echo(format_job_summary(j, done, graph_nodes=graph_nodes))


@cli.group(invoke_without_command=True)
@click.pass_context
def recon(ctx: click.Context) -> None:
    """Recon platform — run, watch, diff, and inspect results."""
    if ctx.invoked_subcommand is None:
        click.echo("Use: wth recon run|quick|watch|diff|changes|plan|show|report|tools")


@recon.command("run")
@click.argument("target")
@click.option("--profile", default="recon-only", show_default=True)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_run(target: str, profile: str, config_path: str) -> None:
    """Run a full recon job for TARGET."""
    _run_recon_job(config_path, target, profile)


@recon.command("quick")
@click.argument("target")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_quick(target: str, config_path: str) -> None:
    """Fast budget-profile recon for TARGET."""
    _run_recon_job(config_path, target, "budget")


@recon.command("watch")
@click.argument("target")
@click.option("--interval", default="6h", show_default=True, help="e.g. 6h, 90m, 1d")
@click.option("--profile", default="recon-only", show_default=True)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_watch(target: str, interval: str, profile: str, config_path: str) -> None:
    """Enable periodic recon watch for TARGET."""
    from wth_ai.cli.recon_commands import recon_watch_text
    from wth_ai.workspace import init_workspace

    init_workspace()
    click.echo(recon_watch_text(target, profile, interval))


@recon.command("diff")
@click.argument("old_job_id")
@click.argument("new_job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_diff(old_job_id: str, new_job_id: str, config_path: str) -> None:
    """Compare normalized results between two recon jobs."""
    from wth_ai.cli.recon_commands import recon_diff_text

    click.echo(recon_diff_text(_job_manager(config_path), old_job_id, new_job_id))


@recon.command("changes")
@click.argument("target")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_changes(target: str, config_path: str) -> None:
    """Show changes between the two latest recon jobs for TARGET."""
    from wth_ai.cli.recon_commands import recon_changes_text

    click.echo(recon_changes_text(_job_manager(config_path), target))


@recon.command("plan")
@click.argument("target")
@click.option("--profile", default="recon-only", show_default=True)
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_plan(target: str, profile: str, config_path: str) -> None:
    """Preview which tools would run for TARGET."""
    from wth_ai.cli.recon_commands import recon_plan_text
    from wth_ai.workspace import init_workspace

    init_workspace()
    click.echo(recon_plan_text(target, profile))


@recon.command("show")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_show(job_id: str, config_path: str) -> None:
    """Show job status and result summary."""
    job = _job_manager(config_path).get_job(job_id)
    if not job:
        click.echo(f"{job_id}: not found")
        return
    click.echo(json.dumps({
        "job_id": job.job_id,
        "status": job.status,
        "target": job.target,
        "result": job.result,
    }, indent=2, default=str))


@recon.command("report")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_report(job_id: str, config_path: str) -> None:
    """Print recon_report.md for a completed job."""
    from wth_ai.cli.recon_commands import recon_report_text

    click.echo(recon_report_text(_job_manager(config_path), job_id))


@recon.group("tools", invoke_without_command=True)
@click.pass_context
def recon_tools(ctx: click.Context) -> None:
    """Recon tool registry (list, available, doctor, enable/disable)."""
    if ctx.invoked_subcommand is None:
        click.echo("Use: wth recon tools list|available|missing|doctor|show|enable|disable")


@recon_tools.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_nested_tools_list(config_path: str) -> None:
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.tools.values(),
                               title=f"Recon tools ({len(reg.tools)} total)"))


@recon_tools.command("available")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_nested_tools_available(config_path: str) -> None:
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.available(), title="Recon tools tersedia"))


@recon_tools.command("missing")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_nested_tools_missing(config_path: str) -> None:
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.missing(), title="Recon tools belum terpasang",
                               include_hint=True))


@recon_tools.command("doctor")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_nested_tools_doctor(config_path: str) -> None:
    click.echo(json.dumps(_recon_registry().summary(), indent=2))


@recon_tools.command("show")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_nested_tools_show(name: str, config_path: str) -> None:
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
def recon_nested_tools_enable(name: str, config_path: str) -> None:
    click.echo(f"enabled {name}" if _recon_registry().enable(name) else f"{name}: not in catalog")


@recon_tools.command("disable")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_nested_tools_disable(name: str, config_path: str) -> None:
    click.echo(f"disabled {name}" if _recon_registry().disable(name) else f"{name}: not in catalog")


@cli.group("recon-tools", invoke_without_command=True)
@click.pass_context
def recon_tools_cli(ctx: click.Context) -> None:
    """Recon tool registry introspection (admin). Use agent chat for recon runs."""
    if ctx.invoked_subcommand is None:
        click.echo("Use: wth recon-tools list|available|doctor")


@recon_tools_cli.command("list")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_list(config_path: str) -> None:
    """List all recon tools grouped by category."""
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.tools.values(),
                               title=f"Recon tools ({len(reg.tools)} total)"))


@recon_tools_cli.command("available")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_available(config_path: str) -> None:
    """List tools available in this environment."""
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.available(), title="Recon tools tersedia"))


@recon_tools_cli.command("missing")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_missing(config_path: str) -> None:
    """List external tools not available."""
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.missing(), title="Recon tools belum terpasang",
                               include_hint=True))


@recon_tools_cli.command("doctor")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_doctor(config_path: str) -> None:
    """Recon registry health summary."""
    click.echo(json.dumps(_recon_registry().summary(), indent=2))


@recon_tools_cli.command("show")
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


@recon_tools_cli.command("enable")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
def recon_tools_enable(name: str, config_path: str) -> None:
    """Enable a tool (persisted override)."""
    click.echo(f"enabled {name}" if _recon_registry().enable(name) else f"{name}: not in catalog")


@recon_tools_cli.command("disable")
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
@click.option("--local", is_flag=True, help="Embedded mode (skip gateway client).")
def interactive(config_path: str, local: bool) -> None:
    """Start interactive REPL."""
    from wth_ai.cli.setup_wizard import ensure_setup
    ensure_setup()
    if local:
        os.environ["WTH_LOCAL"] = "1"
    _orch(config_path).run_interactive(local=local)


@cli.command("run")
@click.argument("command")
@click.option("--config", "config_path", default="config/wth_config.yaml", show_default=True)
@click.option("--fresh", is_flag=True, help="Start new session instead of resuming latest.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON wrapper.")
def run_command(command: str, config_path: str, fresh: bool, as_json: bool) -> None:
    """Run a natural-language agent command (recon, hunt, scope, report, ...)."""
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
    orch.run_once(f"scope set target={target} environment={env} authorized={'yes' if authorized else 'no'}")
    orch.run_once(f"set permission to {permission}")
    console = Console()
    console.print(orch.startup_message())
    if plan:
        console.print(orch.run_once(f"hunt {target} — environment {env}"))
    else:
        console.print("Scope set. Lanjut: wth-ai interactive atau wth run 'recon example.com'")


if __name__ == "__main__":
    cli()
