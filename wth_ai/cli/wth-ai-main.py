#!/usr/bin/env python3
"""WTH-AI v1.0 — Realtime Bug Hunter CLI."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console

_ROOT = Path(__file__).resolve().parent
#: Direktori tempat operator memanggil `wth-ai` — disimpan SEBELUM `os.chdir`, karena
#: semua path relatif yang operator berikan (`--out out/`, `--bundle out/bundle`)
#: harus diartikan relatif ke sana, bukan ke root repo.
_CALLER_CWD = Path(os.getcwd())
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

_WTH_HOME = Path(os.environ.get("WTH_HOME", Path.home() / ".wth-ai"))
load_dotenv(_WTH_HOME / ".env")
load_dotenv(_ROOT / ".env")

from wth_ai import __version__
from wth_ai.config import WTHConfig
from wth_ai.orchestrator import WTHOrchestrator


def _load_config(config_path: str) -> WTHConfig:
    """P1-2: config rusak dilaporkan `path:line`, bukan traceback."""
    from wth_ai.config import ConfigError

    try:
        return WTHConfig.load(config_path)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(2) from None


def _user_path(value: str | Path) -> Path:
    """Path dari operator: relatif ke direktori pemanggil, bukan root repo."""
    path = Path(value)
    return path if path.is_absolute() else (_CALLER_CWD / path)


def _orch(config_path: str, *, fresh: bool = False) -> WTHOrchestrator:
    return WTHOrchestrator(_load_config(config_path), fresh=fresh)


@click.group(name="wth-ai", invoke_without_command=True)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
@click.version_option(version=__version__, prog_name="wth-ai",
                      message="wth-ai %(version)s")
@click.option("--skip-setup", is_flag=True, help="Skip first-run setup check.")
@click.option("--local", is_flag=True, help="Embedded mode (skip gateway client).")
@click.option("--plain", is_flag=True, help="Plain unicode terminal REPL (default).")
@click.option("--tui", is_flag=True, help="Fullscreen Textual TUI (requires wth-ai[tui]).")
@click.option(
    "--bypass", "bypass_flag", is_flag=True,
    help="Aktifkan instruksi sesi bawaan (prompts/agent/AGENT.md).",
)
@click.option(
    "--bypass-profile", "bypass_profile", default="",
    metavar="NAMA",
    help="Aktifkan profil bypass bernama dari $WTH_HOME/bypass.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: str,
    skip_setup: bool,
    local: bool,
    plain: bool,
    tui: bool,
    bypass_flag: bool,
    bypass_profile: str,
) -> None:
    """WTH-AI v1.0 — realtime bug hunter with dual-agent AI."""
    if bypass_profile:
        os.environ["WTH_BYPASS"] = bypass_profile.strip()
    elif bypass_flag:
        os.environ["WTH_BYPASS"] = "default"
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
@click.option("--check", is_flag=True, help="Hanya periksa lingkungan (tanpa menulis apa pun).")
@click.option("--install", "do_install", is_flag=True, help="Pasang dependensi yang belum ada.")
@click.option("--with", "with_features", default="", help="Fitur opsional: tui,bot,browser,full (koma).")
@click.option("--dry-run", is_flag=True, help="Tampilkan perintah yang akan dijalankan.")
@click.option("--no-smoke", is_flag=True, help="Lewati smoke test provider.")
@click.option("--non-interactive", is_flag=True, help="Jangan buka wizard setelah preflight.")
@click.option("--provider", default="", help="Provider untuk non-interaktif (mis. 9router, openrouter, custom).")
@click.option("--base-url", default="", help="Base URL endpoint (dipakai dengan --non-interactive).")
@click.option("--api-key", default="", help="API key; hanya tersimpan di vault, tidak di user.yaml.")
@click.option("--model", default="", help="Model default untuk semua role.")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def setup(
    config_path: str,
    reset: bool,
    reconfigure: bool,
    check: bool,
    do_install: bool,
    with_features: str,
    dry_run: bool,
    no_smoke: bool,
    non_interactive: bool,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
) -> None:
    """Preflight + konfigurasi awal WTH-AI.

    Tanpa flag, perintah ini menjalankan preflight, membuat workspace
    `~/.wth-ai`, lalu wizard provider. `--check` hanya melaporkan.
    """
    from wth_ai.install import build_report, format_report, install_missing
    from wth_ai.vault import SecretVault

    features = [f.strip().lower() for f in with_features.replace(",", " ").split() if f.strip()]
    report = build_report()

    if check or dry_run:
        click.echo(format_report(report))
        if do_install or dry_run:
            code, commands = install_missing(report, features=features, dry_run=True)
            for command in commands:
                click.echo(f"\n  perintah: {command}")
            if not commands:
                click.echo("\n  tidak ada yang perlu dipasang.")
        if not report.ok and not do_install:
            raise SystemExit(1)
        return

    if not report.ok:
        click.echo(format_report(report))
        raise SystemExit(1)

    if do_install or features:
        code, commands = install_missing(report, features=features)
        for command in commands:
            click.echo(f"  $ {command}")
        if code != 0:
            raise SystemExit(code)

    from wth_ai.install import apply_setup

    report, smoke = apply_setup(smoke=not no_smoke)
    click.echo(f"✓ workspace: {report.home}")
    if smoke:
        click.echo(smoke)

    from wth_ai.cli.setup_wizard import build_setup_payload

    wants_scripted = bool(provider or base_url or api_key or model)
    if non_interactive or wants_scripted:
        try:
            payload = build_setup_payload(
                provider=provider, base_url=base_url, api_key=api_key, model=model,
            )
        except ValueError as exc:
            raise SystemExit(f"[x] {exc}")
        if not payload.get("provider_type"):
            click.echo("  (tanpa --provider: konfigurasi provider tidak diubah)")
            return
        if reset and not reconfigure:
            payload["require_llm"] = True

        from wth_ai.cli.setup_wizard import run_setup_wizard

        run_setup_wizard(reset=reset and not reconfigure, non_interactive=payload)
        click.echo(f"✓ setup tersimpan (provider {payload['provider_type']})")
        from wth_ai.config import USER_CONFIG_PATH

        click.echo(f"  config: {USER_CONFIG_PATH}\n  vault : {SecretVault().path}")
        return

    from wth_ai.cli.setup_wizard import run_setup_wizard

    run_setup_wizard(reset=reset and not reconfigure)


@cli.command()
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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


@cli.command("kill")
def kill_cmd() -> None:
    """Hentikan semua worker sesi ini (<=2 detik), tanpa konfirmasi."""
    from wth_ai.killswitch import KILL_SWITCH, request_kill

    stopped = KILL_SWITCH.activate()
    request_kill("cli")
    if stopped:
        click.echo(f"kill: {len(stopped)} proses dihentikan ({', '.join(stopped)})")
    else:
        click.echo("kill: flag dipasang (worker berhenti di iterasi berikutnya)")


@cli.command("scope")
@click.option("--target", required=True, help="Target utama (host/CIDR/URL).")
@click.option("--cidr", "cidrs", multiple=True, help="CIDR yang diizinkan (boleh diulang).")
@click.option("--domain", "domains", multiple=True, help="Domain yang diizinkan (boleh diulang).")
@click.option("--mode", default="plan", show_default=True,
              help="Mode agen yang dicatat di scope card.")
@click.option("--authorized", is_flag=True, help="Konfirmasi izin tertulis.")
@click.option("--destructive", is_flag=True, help="Izinkan aksi mengubah state (lab).")
@click.option("--spec", "specs", multiple=True,
              help="OpenAPI/Swagger atau Postman collection; host-nya masuk scope.")
@click.option("--source", "source_path", default=None,
              help="Direktori codebase yang diizinkan (kind=source).")
def scope_cmd(target: str, cidrs: tuple[str, ...], domains: tuple[str, ...],
              mode: str, authorized: bool, destructive: bool,
              specs: tuple[str, ...], source_path: str | None) -> None:
    """Tulis scope_card.json — rekaman izin yang ikut ke bundel bukti."""
    from wth_ai.scope_card import ScopeCard, ScopeCardError
    from wth_ai.targets.spec import load_spec
    from wth_ai.workspace import wth_home

    if source_path:
        if specs or cidrs or domains:
            click.echo("scope: DITOLAK — --source tidak digabung dengan --cidr/--domain/--spec")
            raise SystemExit(2)
        card = ScopeCard(target=target, kind="source",
                         source_path=str(_user_path(source_path)),
                         authorized=authorized, mode=mode,
                         destructive=destructive)
        try:
            path = card.write(wth_home() / "scope_card.json")
        except ScopeCardError as exc:
            click.echo(f"scope: DITOLAK — {exc}")
            raise SystemExit(2) from exc
        click.echo(f"scope: {target} · source={card.source_path} · "
                   f"authorized=yes · mode={mode} · {path}")
        return

    resolved_domains = list(domains)
    endpoints: list[str] = []
    for spec_path in specs:
        try:
            spec = load_spec(_user_path(spec_path))
        except ValueError as exc:
            click.echo(f"scope: spec DITOLAK — {exc}")
            raise SystemExit(2) from exc
        resolved_domains.extend(host for host in spec.hosts()
                                if host not in resolved_domains)
        endpoints.extend(f"{spec.kind}:{endpoint}" for endpoint in spec.endpoints)

    card = ScopeCard(target=target, cidr=list(cidrs), domains=resolved_domains,
                     authorized=authorized, mode=mode, destructive=destructive,
                     endpoints=endpoints)
    try:
        path = card.write(wth_home() / "scope_card.json")
    except ScopeCardError as exc:
        click.echo(f"scope: DITOLAK — {exc}")
        raise SystemExit(2) from exc
    extra = f" · spec={len(specs)} ({len(endpoints)} endpoint)" if specs else ""
    click.echo(f"scope: {target} · cidr={','.join(cidrs) or '-'} · "
               f"authorized=yes · mode={mode} · {path}{extra}")


@cli.command("scan")
@click.option("--red-team", is_flag=True, help="Jalankan loop red team.")
@click.option("--offline-demo", is_flag=True,
              help="Fixture deterministik tanpa jaringan (untuk demo/slide).")
@click.option("--scan-mode", "scan_mode", default="deep", show_default=True,
              type=click.Choice(["quick", "standard", "deep"]))
@click.option("--max-attempts", default=0, show_default=True,
              help="0 = ikut scan-mode.")
@click.option("--max-turns", default=0, show_default=True, help="0 = tanpa batas.")
@click.option("--target", "targets", multiple=True, help="Target (boleh diulang).")
@click.option("--target-list", default="", help="File berisi target, satu per baris.")
@click.option("--instruction", default="", help="Instruksi operator (teks).")
@click.option("--instruction-file", default="", help="File instruksi operator.")
@click.option("--non-interactive", "-n", is_flag=True,
              help="Headless: exit 2 bila ada temuan (kontrak CI).")
@click.option("--fail-on", default="", help="critical|high|medium|low|info")
@click.option("--scope-mode", "scope_mode", default="full", show_default=True,
              type=click.Choice(["full", "diff"]),
              help="diff = hanya file yang berubah terhadap --diff-base.")
@click.option("--diff-base", "diff_base", default="",
              help="Ref pembanding untuk --scope-mode diff (mis. origin/main).")
@click.option("--verify", "verify_indep", is_flag=True,
              help="Item 4: jalankan verifikasi independen + attestasi.")
@click.option("--out", default="out", show_default=True)
def scan_cmd(red_team: bool, offline_demo: bool, scan_mode: str, max_attempts: int,
             max_turns: int, targets: tuple[str, ...], target_list: str,
             instruction: str, instruction_file: str, non_interactive: bool,
             fail_on: str, scope_mode: str, diff_base: str,
             verify_indep: bool, out: str) -> None:
    """Jalankan loop red team dan tulis artefak + bundel bukti."""
    from wth_ai.cli.inputs import read_instruction, read_targets
    from wth_ai.redteam.artifacts_write import write_findings
    from wth_ai.redteam.bundle import write_bundle
    from wth_ai.redteam.gate import exit_code
    from wth_ai.redteam.runner import run_offline_demo, run_offline_verify
    from wth_ai.redteam.run_meta import STATUS_COMPLETED, STATUS_STOPPED, RunMeta
    from wth_ai.redteam.scan_modes import get_mode
    from wth_ai.reporting.sarif import build_sarif

    if not (red_team or offline_demo):
        raise click.UsageError("pilih --red-team atau --offline-demo")
    if red_team and not offline_demo:
        raise click.UsageError(
            "executor jaringan belum tersambung di build ini — pakai "
            "--offline-demo (deterministik) atau `wth-ai recon quick <target>`"
        )

    mode = get_mode(scan_mode)
    resolved_targets = list(targets)
    if target_list:
        resolved_targets.extend(read_targets(_user_path(target_list)))
    text = read_instruction(_user_path(instruction_file) if instruction_file else None,
                            instruction or None)

    changed: list[str] = []
    if scope_mode == "diff":
        from wth_ai.redteam.diffscope import (DiffScopeError, changed_files,
                                              select)

        try:
            changed = changed_files(_CALLER_CWD, diff_base)
        except DiffScopeError as exc:
            # Gagal membaca diff bukan alasan memindai seluruh repo.
            raise click.ClickException(str(exc)) from exc
        resolved_targets = select(changed, resolved_targets) if resolved_targets else []
        click.echo(f"scope: diff terhadap {diff_base} · {len(changed)} file berubah"
                   f" · {len(resolved_targets)} target")

    result = run_offline_demo(
        max_attempts=max_attempts or mode.max_attempts,
        max_steps=max_turns or mode.max_steps,
    )
    if verify_indep:
        result = run_offline_verify(result)
        click.echo("verify: independen dijalankan atas bukti tersimpan")

    # P0-2: bundel menyalin kartu yang MEMANG ada, bukan mengarang
    # `authorized: true`. Tanpa kartu, demo tetap berjalan tapi jujur soal
    # izinnya, dan CONFIRMED tidak mungkin lolos gate.
    from wth_ai.redteam.classify import enforce_scope
    from wth_ai.scope_card import ScopeCard, ScopeCardError
    from wth_ai.workspace import wth_home

    try:
        card = ScopeCard.load(wth_home() / "scope_card.json")
        bundle_scope = {**card.to_dict(), "demo": bool(offline_demo)}
        scope_authorized = bool(card.authorized)
    except (FileNotFoundError, ScopeCardError, ValueError, OSError):
        bundle_scope = {
            "target": resolved_targets[0] if resolved_targets else "fixture",
            "authorized": False,
            "demo": True,
            "mode": mode.key,
            "scan_mode": mode.key,
            "instruction": text[:500],
        }
        scope_authorized = False
    enforce_scope(result.findings, scope_authorized=scope_authorized)

    out_dir = _user_path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    status = STATUS_STOPPED if result.budget_hit else STATUS_COMPLETED
    meta = RunMeta(
        run_id="RUN-demo",
        targets=resolved_targets or ["fixture"],
        scan_mode=mode.key,
        status=status,
        attempted=result.attempted,
        confirmed=result.confirmed,
        candidate=result.candidate,
        unproven=result.unproven,
        failures=result.failures,
        coverage={"surfaces_total": result.attempted,
                  "surfaces_attempted": result.attempted,
                  "findings_confirmed": result.confirmed},
        scope_mode=scope_mode,
        diff_base=diff_base,
        changed_files=changed,
    )
    meta.write(out_dir / "run.json")
    write_findings(out_dir, result.findings, chains=result.chains)
    (out_dir / "findings.sarif").write_text(
        json.dumps(build_sarif(result.findings, tool_version=__version__),
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = write_bundle(
        out_dir / "bundle",
        findings=result.findings,
        scope_card=bundle_scope,
        audit_events=[{"event": "scan.started", "scan_mode": mode.key},
                      {"event": "scan.finished", "attempted": result.attempted,
                       "confirmed": result.confirmed, "status": status}],
    )

    click.echo(f"scan: {result.attempted} dicoba · {result.confirmed} confirmed · "
               f"{result.candidate} candidate · {result.unproven} unproven · "
               f"mode={mode.key}")
    click.echo(f"bundle: {out_dir / 'bundle'} · root "
               f"{manifest['root_hash'][:12]}")

    if non_interactive:
        raise SystemExit(exit_code(result.findings, fail_on or None,
                                   scope_authorized=scope_authorized))


@cli.command("view")
@click.option("--bundle", default="out/bundle", show_default=True)
@click.option("--port", default=0, show_default=True,
              help="0 = port acak dari OS.")
@click.option("--yes", is_flag=True,
              help="Konfirmasi eksplisit: membuka port HTTP lokal.")
@click.option("--print-only", "print_only", is_flag=True,
              help="Cetak URL + token lalu keluar (tanpa menunggu).")
def view_cmd(bundle: str, port: int, yes: bool, print_only: bool) -> None:
    """Dashboard bundel read-only di 127.0.0.1 (izin berbatas, default mati)."""
    import time

    from wth_ai.view.server import ViewServer

    bundle_dir = _user_path(bundle)
    if not (bundle_dir / "manifest.json").is_file():
        raise click.ClickException(f"bundel tidak ditemukan: {bundle_dir / 'manifest.json'}")
    if not yes:
        raise click.UsageError(
            "butuh --yes: `view` membuka port HTTP di mesin ini. "
            "Hanya bind 127.0.0.1, read-only, token acak per proses."
        )

    server = ViewServer(bundle_dir, port=port)
    url = server.start()
    try:
        click.echo(f"view: {url}")
        click.echo(f"token: {server.token}")
        click.echo("bind 127.0.0.1 · read-only · tanpa rute mutasi · ctrl+c untuk berhenti")
        if print_only:
            return
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        click.echo("view: berhenti")
    finally:
        server.stop()


@cli.command("autofix")
@click.option("--bundle", default="out/bundle", show_default=True)
@click.option("--finding", "finding_id", required=True, help="ID temuan di bundel.")
@click.option("--worktree", required=True, help="Worktree target (terpisah dari repo ini).")
@click.option("--retest", default="", help="Perintah retest; argumen dipisah spasi.")
@click.option("--mode", default="plan", show_default=True)
@click.option("--destructive", is_flag=True,
              help="Wajib: izinkan perubahan file target (hanya berlaku di full_lab).")
def autofix_cmd(bundle: str, finding_id: str, worktree: str, retest: str,
                mode: str, destructive: bool) -> None:
    """Terapkan patch saran temuan di worktree terpisah; tidak pernah push."""
    import shlex

    from wth_ai.autofix.apply import apply_patch
    from wth_ai.autofix.planner import AutofixUnavailable, plan_fix
    from wth_ai.redteam.models import Finding

    findings_path = _user_path(bundle) / "findings.json"
    if not findings_path.is_file():
        raise click.ClickException(f"bundel tidak punya findings.json: {findings_path}")
    try:
        raw = json.loads(findings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"findings.json tidak terbaca: {exc}") from exc

    item = next((f for f in raw if isinstance(f, dict) and str(f.get("id")) == finding_id), None)
    if item is None:
        raise click.ClickException(f"temuan {finding_id} tidak ada di bundel")

    finding = Finding(
        id=str(item.get("id", "")),
        hypothesis=str(item.get("hypothesis", "")),
        vuln_class=str(item.get("vuln_class", "")),
        target=str(item.get("target", "")),
        evidence=[str(e) for e in (item.get("evidence") or [])],
    )
    try:
        patch = plan_fix(finding)
    except AutofixUnavailable as exc:
        raise click.ClickException(str(exc)) from exc

    result = apply_patch(
        patch,
        _user_path(worktree),
        mode=mode,
        destructive=destructive,
        retest_argv=shlex.split(retest) or None,
    )
    if not result.ok:
        click.echo(f"autofix: tidak diterapkan · {result.error}", err=True)
        raise SystemExit(1)
    click.echo(f"autofix: {patch.path} · diterapkan · "
               f"retest={'ya' if retest else 'tidak'} · tanpa push")


@cli.command("bench")
@click.option("--tasks", "tasks_dir", required=True,
              help="Direktori berisi task benchmark (satu subdir per task).")
@click.option("--out", default="out", show_default=True)
def bench_cmd(tasks_dir: str, out: str) -> None:
    """Jalankan benchmark offline; exit 0 bila semua task lulus, 1 bila tidak."""
    from wth_ai.bench.harness import BenchError, run_all

    try:
        results, totals = run_all(_user_path(tasks_dir))
    except BenchError as exc:
        raise click.ClickException(str(exc)) from exc

    out_dir = _user_path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bench.json").write_text(
        json.dumps({"totals": totals, "tasks": [r.to_dict() for r in results]},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    for r in results:
        click.echo(f"{'lulus' if r.passed else 'gagal'}: {r.task} · "
                   f"hit {r.ground_truth_hits}/{r.truth_total} · "
                   f"temuan {len(r.findings)} · {r.duration_s:.2f}s")
    click.echo(f"bench: precision {totals['precision']} · recall {totals['recall']} · "
               f"{totals['passed']}/{totals['tasks']} lulus → {out_dir / 'bench.json'}")
    raise SystemExit(0 if totals["passed"] == totals["tasks"] else 1)


@cli.command("disclosure")
@click.argument("finding_id")
@click.option("--bundle", default="out/bundle", show_default=True)
@click.option("--contact", default="", help="Kontak peneliti untuk advisory.")
@click.option("--out", default="", help="Path keluaran (default: out/disclosure/).")
def disclosure_cmd(finding_id: str, bundle: str, contact: str, out: str) -> None:
    """Tulis advisory pengungkapan untuk satu temuan (markdown)."""
    from wth_ai.redteam.disclosure import build_advisory
    from wth_ai.redteam.models import Finding

    root = _user_path(bundle)
    findings_path = root / "findings.json"
    if not findings_path.is_file():
        raise click.ClickException(f"tidak ada {findings_path}")
    rows = json.loads(findings_path.read_text(encoding="utf-8"))
    match = next((row for row in rows
                  if str(row.get("id", "")).upper() == finding_id.upper()), None)
    if match is None:
        raise click.ClickException(f"temuan {finding_id} tidak ada di bundel")
    text = build_advisory(Finding.from_dict(match), contact=contact)
    target = _user_path(out) if out else root.parent / "disclosure" / f"{finding_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    click.echo(f"disclosure: {target}")


@cli.command("verify")
@click.argument("bundle")
def verify_cmd(bundle: str) -> None:
    """Verifikasi bundel bukti (hash manifest + root hash)."""
    from wth_ai.redteam.bundle import verify_bundle

    root = _user_path(bundle)
    if not root.is_dir():
        raise click.ClickException(f"bundel tidak ditemukan: {root}")
    ok, problems = verify_bundle(root)
    if ok:
        click.echo(f"verify: OK — {bundle}")
        return
    click.echo(f"verify: GAGAL — {bundle}")
    for problem in problems:
        click.echo(f"  - {problem}")
    raise SystemExit(1)


@cli.command("report")
@click.option("--bundle", default="out/bundle", show_default=True)
@click.option("--format", "fmt", default="html", show_default=True,
              type=click.Choice(["html", "sarif", "json", "id", "pdf"]))
@click.option("--out", default="", help="Path keluaran (default: di dalam out/).")
def report_cmd(bundle: str, fmt: str, out: str) -> None:
    """Tulis laporan dari bundel (html statis / sarif / json / id / pdf)."""
    from wth_ai.redteam.models import Finding
    from wth_ai.reporting import html as html_report
    from wth_ai.reporting.id_report import write_id_report
    from wth_ai.reporting.pdf import write_pdf
    from wth_ai.reporting.sarif import build_sarif

    root = _user_path(bundle)
    findings_path = root / "findings.json"
    if not findings_path.is_file():
        raise click.ClickException(f"tidak ada {findings_path}")
    findings = [Finding.from_dict(row) for row in
                json.loads(findings_path.read_text(encoding="utf-8"))]
    default_name = ("report-id.md" if fmt == "id"
                    else "report.pdf" if fmt == "pdf"
                    else f"report.{fmt if fmt != 'html' else 'html'}")
    base = _user_path(out) if out else root.parent / default_name
    if fmt == "html":
        path = html_report.write_html(root, base)
    elif fmt == "sarif":
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(json.dumps(build_sarif(findings, tool_version=__version__),
                                   indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        path = base
    elif fmt == "id":
        run_meta_path = root.parent / "run.json"
        run_meta = (json.loads(run_meta_path.read_text(encoding="utf-8"))
                    if run_meta_path.is_file() else {})
        path = write_id_report(findings, base, run_meta=run_meta)
    elif fmt == "pdf":
        run_meta_path = root.parent / "run.json"
        meta = (json.loads(run_meta_path.read_text(encoding="utf-8"))
                if run_meta_path.is_file() else {})
        root_hash_path = root / "root_hash.txt"
        if root_hash_path.is_file():
            meta["root_hash"] = root_hash_path.read_text(encoding="utf-8").strip()
        path = write_pdf(findings, base, meta=meta)
    else:
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(json.dumps([f.to_dict() for f in findings], indent=2,
                                   ensure_ascii=False) + "\n", encoding="utf-8")
        path = base
    click.echo(f"report: {path}")


@cli.group("kb")
def kb_group() -> None:
    """Knowledge base CVE lokal."""


@kb_group.command("status")
def kb_status() -> None:
    """Jumlah record + berapa yang siap assessment + kapan dibangun."""
    from wth_ai.kb.db import connect, count, get_meta
    from wth_ai.workspace import wth_home

    conn = connect(wth_home() / "kb" / "kb.sqlite")
    total, ready = count(conn)
    click.echo(f"kb: {total} record · {ready} siap-assessment · "
               f"dibangun {get_meta(conn, 'built_at', 'belum pernah')}")


@kb_group.command("get")
@click.argument("cve_id")
def kb_get(cve_id: str) -> None:
    """Tampilkan satu CVE; yang tidak ada di DB ditolak (exit 2)."""
    from wth_ai.kb.db import connect
    from wth_ai.workspace import wth_home

    conn = connect(wth_home() / "kb" / "kb.sqlite")
    row = conn.execute("SELECT * FROM cve WHERE cve_id=?",
                       (cve_id.upper(),)).fetchone()
    if row is None:
        click.echo(f"kb: {cve_id} TIDAK ADA di basis data — sitasi ditolak")
        raise SystemExit(2)
    for key in row.keys():
        click.echo(f"{key}: {row[key]}")


@kb_group.command("build")
@click.option("--source", required=True,
              help="Direktori berisi nvd/*.json, kev.json, epss.csv (sudah diunduh).")
def kb_build(source: str) -> None:
    """Isi KB dari file mentah. Tidak mengunduh apa pun."""
    from wth_ai.kb.build import build
    from wth_ai.kb.db import connect
    from wth_ai.workspace import wth_home

    conn = connect(wth_home() / "kb" / "kb.sqlite")
    try:
        summary = build(_user_path(source), conn)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"kb: {summary['total']} record · {summary['ready']} siap-assessment"
               f" · {summary['kev']} KEV · dibangun dari {source}")


@kb_group.command("install")
@click.option("--bundle", required=True, help="Direktori bundle KB (kb.sqlite + manifest.json).")
def kb_install(bundle: str) -> None:
    """Pasang KB dari bundle air-gap, setelah verifikasi sha256."""
    from wth_ai.kb.bundle import BundleError, install_bundle
    from wth_ai.kb.db import connect, count
    from wth_ai.workspace import wth_home

    root = _user_path(bundle)
    dest = wth_home() / "kb" / "kb.sqlite"
    try:
        manifest = install_bundle(root, dest)
    except BundleError as exc:
        raise click.ClickException(f"kb install: {exc}") from exc
    conn = connect(dest)
    total, ready = count(conn)
    click.echo(f"kb: terpasang {dest}")
    click.echo(f"kb: {total} record · {ready} siap-assessment · "
               f"dibangun {manifest.get('built_at', '?')}")


@kb_group.command("search")
@click.argument("query")
@click.option("--limit", default=10, show_default=True)
def kb_search(query: str, limit: int) -> None:
    """Cari di KB (FTS5). Hasil kosong berarti tidak ada dasar untuk dikutip."""
    from wth_ai.kb.db import connect
    from wth_ai.workspace import wth_home

    tokens = [token for token in "".join(
        ch if ch.isalnum() else " " for ch in query).split() if len(token) > 2]
    if not tokens:
        raise click.ClickException("query terlalu pendek")
    conn = connect(wth_home() / "kb" / "kb.sqlite")
    rows = conn.execute(
        "SELECT c.cve_id AS cve_id, c.cvss AS cvss, c.kev AS kev, "
        "c.product AS product, c.summary AS summary "
        "FROM cve_fts JOIN cve c ON c.cve_id = cve_fts.cve_id "
        "WHERE cve_fts MATCH ? ORDER BY bm25(cve_fts) LIMIT ?",
        (" OR ".join(tokens[:8]), limit),
    ).fetchall()
    if not rows:
        click.echo(f"kb: tidak ada hasil untuk '{query}' — jangan dikutip")
        raise SystemExit(1)
    for row in rows:
        flag = "KEV" if row["kev"] else "   "
        click.echo(f"{row['cve_id']}  {flag}  cvss={row['cvss'] or '-'}  "
                   f"{row['product'] or '-'}  {row['summary'][:80]}")


@cli.group("ot")
def ot_group() -> None:
    """Recon OT/ICS — hanya baca, tidak pernah menulis ke PLC."""


@ot_group.command("read")
@click.option("--host", required=True)
@click.option("--cidr", "cidrs", multiple=True, required=True,
              help="Allowlist egress (wajib; minimal satu CIDR).")
@click.option("--kind", default="holding", show_default=True,
              type=click.Choice(["holding", "input", "coils", "discrete_inputs"]))
@click.option("--address", default=0, show_default=True)
@click.option("--count", default=4, show_default=True)
@click.option("--unit", default=1, show_default=True)
@click.option("--port", default=502, show_default=True)
@click.option("--confirm", is_flag=True, help="Konfirmasi koneksi ke perangkat.")
@click.option("--out", default="", help="Tulis hasil JSON ke path ini.")
def ot_read(host: str, cidrs: tuple[str, ...], kind: str, address: int, count: int,
            unit: int, port: int, confirm: bool, out: str) -> None:
    """Baca blok Modbus (read-only). Tanpa --confirm tidak ada koneksi."""
    import json as _json

    from wth_ai.netguard import EgressDenied, EgressGuard
    from wth_ai.ot.client import ModbusError, read_registers

    if not confirm:
        click.echo("ot read: DITOLAK — butuh --confirm (koneksi ke perangkat)")
        raise SystemExit(2)

    guard = EgressGuard.from_patterns(list(cidrs))
    try:
        result = read_registers(host, kind=kind, address=address, count=count,
                                unit=unit, port=port, guard=guard)
    except EgressDenied as exc:
        click.echo(f"ot read: EGRESS DENIED — {exc}")
        raise SystemExit(2) from exc
    except (ModbusError, OSError) as exc:
        click.echo(f"ot read: GAGAL — {exc}")
        raise SystemExit(1) from exc

    payload = result.to_dict()
    click.echo(f"ot read: {host}:{port} unit={unit} {kind}[{address}:{address + count}]"
               f" · {result.latency_ms} ms · destructive=False")
    click.echo("  values: " + ", ".join(str(value) for value in result.values))
    if out:
        target = _user_path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        click.echo(f"ot read: {target}")


@cli.group("browser")
def browser_group() -> None:
    """Screenshot lewat Playwright CLI (opsional; tidak ada import browser)."""


@browser_group.command("capture")
@click.argument("url")
@click.option("--out", default="out/screenshots", show_default=True)
@click.option("--name", default="", help="Nama file tanpa ekstensi.")
@click.option("--timeout", default=45, show_default=True)
def browser_capture(url: str, out: str, name: str, timeout: int) -> None:
    """Ambil satu screenshot. Gagal -> exit 1 dengan alasan, bukan traceback."""
    from wth_ai.recon.browser import capture

    result = capture(url, _user_path(out), name=name, timeout=timeout)
    if not result["ok"]:
        click.echo(f"browser: GAGAL — {result['error']}")
        raise SystemExit(1)
    click.echo(f"browser: {result['path']} · {result['bytes']} byte · {result['ms']} ms")


@cli.group("sandbox")
def sandbox_group() -> None:
    """Pratinjau dan jalankan sandbox container."""


@sandbox_group.command("run")
@click.option("--workspace-file", "workspace_files", multiple=True,
              help="File yang di-mount read-only ke /work/<nama>.")
@click.option("--mount", "mounts", multiple=True, help="Mount tambahan SRC:DST[:ro|rw].")
@click.option("--image", default="", help="Override image (dukung image@sha256:...).")
@click.option("--network", default="", help="Override nama network lab.")
@click.option("--yes", is_flag=True, help="Konfirmasi menjalankan docker.")
@click.argument("command", nargs=-1)
def sandbox_run(workspace_files: tuple[str, ...], mounts: tuple[str, ...],
                image: str, network: str, yes: bool,
                command: tuple[str, ...]) -> None:
    """Jalankan container sandbox. Butuh --yes; tanpa itu hanya mencetak argv."""
    import subprocess

    from wth_ai.containment import SandboxSpec

    extra: list[str] = []
    for raw in mounts:
        if len(raw.split(":")) < 2:
            raise click.ClickException(f"mount tidak valid: {raw} (SRC:DST[:ro|rw])")
        extra.append(raw if len(raw.split(":")) > 2 else f"{raw}:ro")
    for path in workspace_files:
        source = _user_path(path)
        if not source.exists():
            raise click.ClickException(f"workspace file tidak ada: {source}")
        extra.append(f"{source}:/work/{source.name}:ro")

    kwargs: dict = {"extra_mounts": tuple(extra)}
    if image:
        # B5: resolver digest saat --image dipakai (tag -> digest), tolak bila
        # tidak bisa diverifikasi. Default image tidak lewat sini; pin lewat
        # WTH_LAB_IMAGE tetap diverifikasi di SandboxSpec.
        from wth_ai.containment import resolve_image_digest

        ok, resolved = resolve_image_digest(image)
        if not ok:
            raise click.ClickException(f"sandbox: {resolved}")
        kwargs["image"] = resolved
    if network:
        kwargs["network"] = network
    argv = SandboxSpec(**kwargs).docker_args(list(command) or ["sh", "-lc", "true"])

    if not yes:
        click.echo("sandbox: DITOLAK — butuh --yes untuk menjalankan container")
        click.echo("sandbox: argv = " + " ".join(argv))
        raise SystemExit(2)
    if not shutil.which(argv[0]):
        raise click.ClickException(f"{argv[0]} tidak ditemukan di PATH")
    completed = subprocess.run(argv, check=False)
    raise SystemExit(completed.returncode)


@sandbox_group.command("plan")
@click.option("--workspace-file", "workspace_files", multiple=True,
              help="File yang di-mount read-only ke /work/<nama> (boleh diulang).")
@click.option("--mount", "mounts", multiple=True,
              help="Mount tambahan SRC:DST[:ro|rw] (boleh diulang).")
@click.option("--image", default="", help="Override image (dukung image@sha256:...).")
@click.option("--network", default="", help="Override nama network lab.")
@click.argument("command", nargs=-1)
def sandbox_plan(workspace_files: tuple[str, ...], mounts: tuple[str, ...],
                 image: str, network: str, command: tuple[str, ...]) -> None:
    """Cetak argv `docker run` untuk sandbox. Tidak menjalankan docker."""
    import shlex

    from wth_ai.containment import SandboxSpec

    extra: list[str] = []
    for raw in mounts:
        parts = raw.split(":")
        if len(parts) < 2:
            raise click.ClickException(f"mount tidak valid: {raw} (SRC:DST[:ro|rw])")
        spec = raw if len(parts) > 2 else f"{raw}:ro"
        extra.append(spec)
    for path in workspace_files:
        source = _user_path(path)
        if not source.exists():
            raise click.ClickException(f"workspace file tidak ada: {source}")
        extra.append(f"{source}:/work/{source.name}:ro")

    kwargs: dict = {"extra_mounts": tuple(extra)}
    if image:
        kwargs["image"] = image
    if network:
        kwargs["network"] = network
    spec = SandboxSpec(**kwargs)
    argv = spec.docker_args(list(command) or ["sh", "-lc", "true"])
    click.echo(" ".join(shlex.quote(token) for token in argv))
    click.echo(f"sandbox: {len(extra)} mount · destructive=False · tidak dijalankan")


@cli.group("visual")
def visual_group() -> None:
    """Recon visual — clustering screenshot tanpa dependency gambar."""


@visual_group.command("cluster")
@click.argument("directory")
@click.option("--threshold", default=6, show_default=True,
              help="Jarak Hamming maksimum agar dianggap satu klaster.")
@click.option("--out", default="", help="Tulis hasil JSON ke path ini.")
def visual_cluster(directory: str, threshold: int, out: str) -> None:
    """Kelompokkan PNG di DIRECTORY berdasarkan perceptual hash."""
    from wth_ai.recon.visual import scan_directory

    root = _user_path(directory)
    if not root.is_dir():
        raise click.ClickException(f"direktori tidak ada: {root}")
    result = scan_directory(root, threshold=threshold)
    click.echo(f"visual: {result['total']} gambar · {len(result['clusters'])} klaster"
               f" · terbesar {result['largest_cluster']} · dilewati {len(result['skipped'])}")
    for index, group in enumerate(result["clusters"][:10], start=1):
        click.echo(f"  klaster {index}: {group['size']} gambar "
                   f"(mis. {Path(group['members'][0]).name})")
    if out:
        target = _user_path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
        click.echo(f"visual: {target}")


@ot_group.command("plan")
@click.option("--host", required=True, help="Host PLC/RTU (lab atau berizin).")
@click.option("--unit", default=1, show_default=True, help="Modbus unit id (1..247).")
@click.option("--block", "blocks", multiple=True,
              help="kind:address:count, mis. holding:0:10 (boleh diulang).")
def ot_plan(host: str, unit: int, blocks: tuple[str, ...]) -> None:
    """Tampilkan rencana baca Modbus. Tidak membuka koneksi."""
    from wth_ai.ot.modbus import plan_reads

    parsed: list[tuple[str, int, int]] = []
    for raw in blocks:
        parts = raw.split(":")
        if len(parts) != 3:
            raise click.ClickException(f"block tidak valid: {raw} (kind:address:count)")
        kind, address, count = parts
        try:
            parsed.append((kind, int(address), int(count)))
        except ValueError as exc:
            raise click.ClickException(f"block tidak valid: {raw}") from exc

    try:
        requests = (plan_reads(host, unit=unit, blocks=parsed) if parsed
                    else plan_reads(host, unit=unit))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"ot plan: {host} · unit={unit} · {len(requests)} permintaan baca")
    for request in requests:
        click.echo(f"  fc={request.function_code} {request.kind:<16} "
                   f"addr={request.address:<6} count={request.count:<4} "
                   f"destructive={request.destructive}")
    click.echo("ot plan: tidak ada koneksi dibuka; write (FC 5/6/15/16) ditolak")


@cli.group("skill")
def skill_group() -> None:
    """Kelola skill (SKILL.md). Skill tidak pernah dieksekusi."""


@skill_group.command("list")
def skill_list() -> None:
    from wth_ai.skills.registry import SkillRegistry

    registry = SkillRegistry()
    registry.discover()
    click.echo(registry.format_catalog())


@skill_group.command("install")
@click.argument("url")
def skill_install(url: str) -> None:
    """Clone skill ke $WTH_HOME/skills/<nama> lalu tampilkan SKILL.md."""
    import subprocess

    from wth_ai.workspace import wth_home

    dest = wth_home() / "skills"
    dest.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    target = dest / name
    if target.exists():
        raise click.ClickException(f"{target} sudah ada — hapus dulu")
    subprocess.run(["git", "clone", "--depth", "1", url, str(target)], check=True)
    skill_md = target / "SKILL.md"
    if not skill_md.is_file():
        click.echo(f"peringatan: {target} tidak punya SKILL.md")
        return
    click.echo(skill_md.read_text(encoding="utf-8")[:2000])


@cli.group("autopilot", invoke_without_command=True)
@click.option("--target", default=None, help="Target (default: dari scope tersimpan).")
@click.option("--steps", default=6, show_default=True, help="Langkah per cycle.")
@click.option("--cycles", default=2, show_default=True, help="Jumlah pass adaptif.")
@click.option("--dry-run", is_flag=True, help="Susun rencana tanpa menjalankan apa pun.")
@click.option("--every", default=None, help="Daftarkan sebagai cron, mis. 6h / 30m.")
def autopilot_group(target: str | None, steps: int, cycles: int, dry_run: bool, every: str | None) -> None:
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
    if every:
        from wth_ai.cron.store import CronStore

        store = CronStore()
        entry = store.add(target=chosen, schedule=f"every {every}",
                          job_type="autopilot", profile="recon-only")
        click.echo(f"Cron autopilot terdaftar: {entry.cron_id} · {entry.schedule} · target {chosen}")
        click.echo("Ticker akan menjalankan autopilot berbatas sesuai jadwal. "
                   "Gerbang destruktif tetap dibaca dari scope saat job berjalan.")
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


@payloads_group.command("gen")
@click.option("--class", "vuln_class", required=True, help="Kelas kerentanan.")
@click.option("--goal", required=True, help="Tujuan pengujian menurut operator.")
@click.option("--tech", "tech_stack", default="", help="Teknologi target (opsional).")
@click.option("--count", default=3, show_default=True)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def payloads_gen(vuln_class: str, goal: str, tech_stack: str, count: int, config_path: str) -> None:
    """Minta AI menyusun payload BARU, lalu validasi + simpan ke store generate."""
    from wth_ai.hunt.generator import format_generation, generate_with_llm, save_generated
    from wth_ai.llm.router import LLMProviderRouter

    router = LLMProviderRouter(_load_config(config_path))
    result = generate_with_llm(router, vuln_class=vuln_class, goal=goal,
                               tech_stack=tech_stack, count=count)
    if result.accepted:
        path = save_generated(result.accepted)
        result.saved_path = str(path) if path else ""
    click.echo(format_generation(result))


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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def provider_test(name: str, model: str | None, config_path: str) -> None:
    """Test provider reachability (and optional completion)."""
    from wth_ai.llm.router import LLMProviderRouter
    result = LLMProviderRouter(_load_config(config_path)).test_provider(name, model)
    click.echo(json.dumps(result, indent=2, default=str))


@provider.group("keys")
def provider_keys() -> None:
    """Manage provider API keys."""


@provider_keys.command("list")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def provider_keys_set(name: str, config_path: str) -> None:
    provider_add(name, config_path)


@provider_keys.command("test")
@click.argument("name", required=False, default="--all")
@click.option("--model", default=None)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--authorized/--no-authorized", default=None,
              help="Wajib eksplisit: P0-4 tidak ada izin implisit.")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def engage_start(mode: str, target: str, authorized: bool | None, config_path: str) -> None:
    from wth_ai.audit import audit_log
    from wth_ai.engagement.mode import EngagementMode, set_engagement_mode
    from wth_ai.profiles import apply_profile

    # P0-4: absennya flag adalah penolakan, bukan izin. Satu perintah tidak
    # boleh menyalakan dimensi izin, capability, dan approval sekaligus;
    # operator menaikkan capability sendiri lewat `safety unrestricted on`.
    if authorized is None:
        raise click.UsageError(
            "engage start butuh --authorized atau --no-authorized secara eksplisit"
        )

    profile = "ctf-aggressive" if mode == "ctf" else "bug-bounty"
    apply_profile(profile)
    set_engagement_mode(EngagementMode.CTF if mode == "ctf" else EngagementMode.BUG_BOUNTY)
    orchestrator = _orch(config_path, fresh=True)
    orchestrator.run_once(
        f"/scope set target={target} authorized={'yes' if authorized else 'no'} environment={mode}"
    )
    audit_log("engage.started", mode=mode, target=target,
              authorized=authorized, profile=profile)
    click.echo(f"Engagement started: mode={mode} target={target} profile={profile}")


@cli.group()
def mcp() -> None:
    """MCP server integration."""


@mcp.command("list")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def mcp_list(config_path: str) -> None:
    from wth_ai.mcp.manager import MCPClientManager
    mgr = MCPClientManager(_load_config(config_path))
    click.echo(mgr.format_status())


@mcp.command("status")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def mcp_status(config_path: str) -> None:
    mcp_list(config_path)


@mcp.command("tools")
@click.option("--server", default=None)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def mcp_test(server: str, config_path: str) -> None:
    from wth_ai.mcp.manager import MCPClientManager
    mgr = MCPClientManager(_load_config(config_path))
    status = mgr.connect_all()
    click.echo(json.dumps(status, indent=2))


@mcp.command("call")
@click.argument("server")
@click.argument("tool")
@click.option("--args", "args_json", default="{}")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def provider_models(name: str, config_path: str) -> None:
    """List models offered by a provider."""
    from wth_ai.llm.router import LLMProviderRouter
    models = LLMProviderRouter(_load_config(config_path)).list_models(name)
    click.echo("\n".join(models) if models else "(no models returned)")


@cli.group()
def model() -> None:
    """Inspect and set per-role models."""


@model.command("list")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def integration_list(config_path: str) -> None:
    """List integration adapters and their status (tokens masked)."""
    from wth_ai.integrations.manager import IntegrationManager
    mgr = IntegrationManager(_load_config(config_path))
    for st in mgr.status():
        click.echo(f"- {st['name']}: {'enabled' if st['enabled'] else 'disabled'} "
                   f"recipients={st['recipients']} token={st['token'] or 'none'}")


@integration.command("status")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def integration_status(config_path: str) -> None:
    """Show integration status as JSON (tokens masked)."""
    from wth_ai.integrations.manager import IntegrationManager
    click.echo(json.dumps(IntegrationManager(_load_config(config_path)).status(), indent=2))


@integration.command("test")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def telegram_test(config_path: str) -> None:
    """Dry-run a Telegram notification."""
    from wth_ai.integrations.manager import IntegrationManager
    from wth_ai.integrations.telegram import TelegramNotifier
    config = _load_config(config_path)
    mgr = IntegrationManager(config, adapters=[TelegramNotifier(config.integrations.telegram)])
    click.echo(json.dumps(mgr.test(dry_run=True), indent=2))


@telegram.command("enable")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def telegram_enable(config_path: str) -> None:
    """Enable the Telegram integration."""
    _set_integration_enabled(config_path, "telegram", True)


@telegram.command("disable")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def telegram_disable(config_path: str) -> None:
    """Disable the Telegram integration."""
    _set_integration_enabled(config_path, "telegram", False)


@integration.group()
def discord() -> None:
    """Discord integration management."""


@discord.command("setup")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def discord_test(config_path: str) -> None:
    """Dry-run a Discord notification."""
    from wth_ai.integrations.manager import IntegrationManager
    from wth_ai.integrations.discord import DiscordNotifier
    config = _load_config(config_path)
    mgr = IntegrationManager(config, adapters=[DiscordNotifier(config.integrations.discord)])
    click.echo(json.dumps(mgr.test(dry_run=True), indent=2))


@discord.command("enable")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def discord_enable(config_path: str) -> None:
    """Enable the Discord integration."""
    _set_integration_enabled(config_path, "discord", True)


@discord.command("disable")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def job_create(job_type: str, target, profile, priority: str, requires_approval: bool, config_path: str) -> None:
    """Create a JOB_TYPE job (recon|executor|report|provider_test|custom)."""
    mgr = _job_manager(config_path)
    # `wth-ai job create` is the operator naming the target directly, so the
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def job_show(job_id: str, config_path: str) -> None:
    """Show a job as JSON."""
    mgr = _job_manager(config_path)
    j = mgr.get_job(job_id)
    click.echo(json.dumps(j.to_dict(), indent=2, default=str) if j else f"{job_id}: not found")


@job.command("logs")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def job_logs(job_id: str, config_path: str) -> None:
    """Print a job's log."""
    mgr = _job_manager(config_path)
    text = mgr.logs(job_id)
    click.echo(text if text else f"(no logs for {job_id})")


@job.command("stop")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def job_stop(job_id: str, config_path: str) -> None:
    """Stop a job."""
    mgr = _job_manager(config_path)
    j = mgr.stop_job(job_id)
    click.echo(f"{job_id} -> {j.status}" if j else f"{job_id}: not found")


@job.command("retry")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def job_retry(job_id: str, config_path: str) -> None:
    """Re-queue a failed/stopped job."""
    mgr = _job_manager(config_path)
    j = mgr.retry_job(job_id)
    click.echo(f"{job_id} -> {j.status}" if j else f"{job_id}: not found")


@job.command("delete")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
        click.echo("  socket: ~/.wth-ai/runtime/gateway.sock")
        click.echo("  logs:   ~/.wth-ai/logs/gateway.log")
        click.echo("  stop:   wth-ai gateway stop")
        return
    if is_gateway_running():
        pid = read_gateway_pid()
        click.echo(f"gateway already running (pid {pid}) — use `wth-ai gateway stop` first")
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
        click.echo("  start: wth-ai gateway run --background")
        return
    click.echo(json.dumps(info, indent=2, default=str))


@cli.group()
def worker() -> None:
    """Run and inspect the job worker (deprecated — prefer `wth-ai gateway run`)."""


@worker.command("run")
@click.option("--no-drain", is_flag=True, help="Run as a daemon loop instead of draining once.")
@click.option("--max-jobs", default=None, type=int)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def worker_run(no_drain: bool, max_jobs, config_path: str) -> None:
    """Process queued jobs (drains by default; --no-drain loops as a daemon)."""
    click.echo("Note: prefer `wth-ai gateway run` — worker is embedded in the gateway.", err=True)
    from wth_ai.jobs.worker import WorkerEngine
    w = WorkerEngine(_job_manager(config_path))
    summary = w.run(drain=not no_drain, max_jobs=max_jobs)
    click.echo(json.dumps(summary))


@worker.command("run-once")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def worker_run_once(config_path: str) -> None:
    """Process a single job and report what happened."""
    from wth_ai.jobs.worker import WorkerEngine
    w = WorkerEngine(_job_manager(config_path))
    click.echo(json.dumps(w.run_once(), indent=2, default=str))


@worker.command("status")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def worker_status(config_path: str) -> None:
    """Show worker + job summary."""
    from wth_ai.jobs.worker import WorkerEngine
    w = WorkerEngine(_job_manager(config_path))
    click.echo(json.dumps(w.status(), indent=2, default=str))


@worker.command("stop")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
        # P0-1: penolakan scope adalah kegagalan perintah, bukan catatan info.
        click.echo(f"Rejected: {j.error} (job {j.job_id})")
        raise SystemExit(2)
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
        click.echo("Use: wth-ai recon run|quick|watch|diff|changes|plan|show|report|tools")


@recon.command("run")
@click.argument("target")
@click.option("--profile", default="recon-only", show_default=True)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_run(target: str, profile: str, config_path: str) -> None:
    """Run a full recon job for TARGET."""
    _run_recon_job(config_path, target, profile)


@recon.command("quick")
@click.argument("target")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_quick(target: str, config_path: str) -> None:
    """Fast budget-profile recon for TARGET."""
    _run_recon_job(config_path, target, "budget")


@recon.command("watch")
@click.argument("target")
@click.option("--interval", default="6h", show_default=True, help="e.g. 6h, 90m, 1d")
@click.option("--profile", default="recon-only", show_default=True)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_watch(target: str, interval: str, profile: str, config_path: str) -> None:
    """Enable periodic recon watch for TARGET."""
    from wth_ai.cli.recon_commands import recon_watch_text
    from wth_ai.workspace import init_workspace

    init_workspace()
    click.echo(recon_watch_text(target, profile, interval))


@recon.command("diff")
@click.argument("old_job_id")
@click.argument("new_job_id")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_diff(old_job_id: str, new_job_id: str, config_path: str) -> None:
    """Compare normalized results between two recon jobs."""
    from wth_ai.cli.recon_commands import recon_diff_text

    click.echo(recon_diff_text(_job_manager(config_path), old_job_id, new_job_id))


@recon.command("changes")
@click.argument("target")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_changes(target: str, config_path: str) -> None:
    """Show changes between the two latest recon jobs for TARGET."""
    from wth_ai.cli.recon_commands import recon_changes_text

    click.echo(recon_changes_text(_job_manager(config_path), target))


@recon.command("plan")
@click.argument("target")
@click.option("--profile", default="recon-only", show_default=True)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_plan(target: str, profile: str, config_path: str) -> None:
    """Preview which tools would run for TARGET."""
    from wth_ai.cli.recon_commands import recon_plan_text
    from wth_ai.workspace import init_workspace

    init_workspace()
    click.echo(recon_plan_text(target, profile))


@recon.command("show")
@click.argument("job_id")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_report(job_id: str, config_path: str) -> None:
    """Print recon_report.md for a completed job."""
    from wth_ai.cli.recon_commands import recon_report_text

    click.echo(recon_report_text(_job_manager(config_path), job_id))


@recon.group("tools", invoke_without_command=True)
@click.pass_context
def recon_tools(ctx: click.Context) -> None:
    """Recon tool registry (list, available, doctor, enable/disable)."""
    if ctx.invoked_subcommand is None:
        click.echo("Use: wth-ai recon tools list|available|missing|doctor|show|enable|disable")


@recon_tools.command("list")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_nested_tools_list(config_path: str) -> None:
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.tools.values(),
                               title=f"Recon tools ({len(reg.tools)} total)"))


@recon_tools.command("available")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_nested_tools_available(config_path: str) -> None:
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.available(), title="Recon tools tersedia"))


@recon_tools.command("missing")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_nested_tools_missing(config_path: str) -> None:
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.missing(), title="Recon tools belum terpasang",
                               include_hint=True))


@recon_tools.command("doctor")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_nested_tools_doctor(config_path: str) -> None:
    click.echo(json.dumps(_recon_registry().summary(), indent=2))


@recon_tools.command("show")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_nested_tools_enable(name: str, config_path: str) -> None:
    click.echo(f"enabled {name}" if _recon_registry().enable(name) else f"{name}: not in catalog")


@recon_tools.command("disable")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_nested_tools_disable(name: str, config_path: str) -> None:
    click.echo(f"disabled {name}" if _recon_registry().disable(name) else f"{name}: not in catalog")


@cli.group("recon-tools", invoke_without_command=True)
@click.pass_context
def recon_tools_cli(ctx: click.Context) -> None:
    """Recon tool registry introspection (admin). Use agent chat for recon runs."""
    if ctx.invoked_subcommand is None:
        click.echo("Use: wth-ai recon-tools list|available|doctor")


@recon_tools_cli.command("list")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_tools_list(config_path: str) -> None:
    """List all recon tools grouped by category."""
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.tools.values(),
                               title=f"Recon tools ({len(reg.tools)} total)"))


@recon_tools_cli.command("available")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_tools_available(config_path: str) -> None:
    """List tools available in this environment."""
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.available(), title="Recon tools tersedia"))


@recon_tools_cli.command("missing")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_tools_missing(config_path: str) -> None:
    """List external tools not available."""
    reg = _recon_registry()
    click.echo(_registry_table(reg, reg.missing(), title="Recon tools belum terpasang",
                               include_hint=True))


@recon_tools_cli.command("doctor")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_tools_doctor(config_path: str) -> None:
    """Recon registry health summary."""
    click.echo(json.dumps(_recon_registry().summary(), indent=2))


@recon_tools_cli.command("show")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_tools_enable(name: str, config_path: str) -> None:
    """Enable a tool (persisted override)."""
    click.echo(f"enabled {name}" if _recon_registry().enable(name) else f"{name}: not in catalog")


@recon_tools_cli.command("disable")
@click.argument("name")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def recon_tools_disable(name: str, config_path: str) -> None:
    """Disable a tool (persisted override)."""
    click.echo(f"disabled {name}" if _recon_registry().disable(name) else f"{name}: not in catalog")


@cli.group()
def bot() -> None:
    """Discord / Telegram bot daemons."""


@bot.command("discord")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def bot_discord(config_path: str) -> None:
    """Start Discord bot daemon."""
    from wth_ai.bots.discord_bot import run_discord_bot
    run_discord_bot(_load_config(config_path))


@bot.command("telegram")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def bot_telegram(config_path: str) -> None:
    """Start Telegram bot daemon."""
    from wth_ai.bots.telegram_bot import run_telegram_bot
    run_telegram_bot(_load_config(config_path))


@bot.command("status")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
@click.option("--local", is_flag=True, help="Embedded mode (skip gateway client).")
@click.option("--redteam", is_flag=True, help="TUI red team (tata letak coding agent).")
@click.option("--mode", "agent_mode", default="plan", show_default=True,
              type=click.Choice(["plan", "build", "auto", "scout", "strike",
                                 "chain", "scribe"]))
def interactive(config_path: str, local: bool, redteam: bool,
                agent_mode: str) -> None:
    """Start interactive REPL."""
    from wth_ai.cli.setup_wizard import ensure_setup

    if redteam:
        from wth_ai.cli.tui.redteam.app import run as run_redteam

        run_redteam(mode=agent_mode)
        return
    ensure_setup()
    if local:
        os.environ["WTH_LOCAL"] = "1"
    _orch(config_path).run_interactive(local=local)


@cli.command("run")
@click.argument("command")
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def new_session_cmd(config_path: str) -> None:
    """Start a fresh session."""
    sid = _orch(config_path, fresh=True).new_session()
    click.echo(f"New session: {sid}")


@cli.command()
@click.argument("session_id", required=False)
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
def resume(session_id: str | None, config_path: str) -> None:
    """Resume a saved session."""
    click.echo(_orch(config_path).resume(session_id))


@cli.command()
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
@click.option("--config", "config_path", default="config/wth_ai_config.yaml", show_default=True)
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
    orch.run_once(f"scope set target={target} environment={env} authorized={'yes' if authorized else 'no'}")
    orch.run_once(f"set permission to {permission}")
    console = Console()
    console.print(orch.startup_message())
    if plan:
        console.print(orch.run_once(f"hunt {target} — environment {env}"))
    else:
        console.print("Scope set. Lanjut: wth-ai interactive atau wth-ai run 'recon example.com'")


if __name__ == "__main__":
    # Click menurunkan `Usage:` dari sys.argv[0]; launcher bernama wth-ai-main.py,
    # jadi nama kanonik harus dipasok eksplisit.
    cli(prog_name="wth-ai")
