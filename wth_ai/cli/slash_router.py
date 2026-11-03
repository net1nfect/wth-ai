"""Slash command router v2 — deterministic handlers before supervisor."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Callable

from wth_ai.platform.registry import get_platform_registry


Handler = Callable[[list[str]], str]


def _render_help(topic: str | None = None) -> str:
    """Satu implementasi untuk semua permukaan `/help` (router + handler)."""
    registry = get_platform_registry()
    t = (topic or "").strip().lower()
    if not t:
        return registry.help_text(compact=True)
    if t in {"full", "all", "verbose"}:
        return registry.help_text(compact=False)
    return registry.help_text(t, compact=False)


def _unknown_command_message(cmd: str) -> str:
    """Pesan untuk command tak dikenal, dengan saran terdekat bila ada.

    Sebelumnya selalu "Unknown command. Try /help or Ctrl+P" tanpa satu pun
    petunjuk command yang MIRIP, padahal salah ketik (`/recon` -> `/rcon`) adalah
    kasus paling umum. Sekarang: daftar pendek saran + arah ke /help.
    """
    try:
        from wth_ai.platform.registry import get_platform_registry

        picks = get_platform_registry().suggestions(cmd, limit=5)
    except Exception:  # noqa: BLE001 - pesan error tidak boleh ikut error
        picks = []
    if picks:
        lines = [f"Command `{cmd}` tidak dikenal. Mungkin maksudmu:"]
        lines += [f"  {p}" for p in picks]
        lines.append("Ketik `/help` untuk daftar lengkap, atau `/` untuk menu (mode interaktif).")
        return "\n".join(lines)
    return f"Command `{cmd}` tidak dikenal. Ketik `/help` untuk daftar lengkap, atau `/` untuk menu."


@dataclass
class SlashRouter:
    """Route `/command` input to orchestrator handlers."""

    handlers: dict[str, Handler]
    registry: Any = None

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = get_platform_registry()

    def parse(self, text: str) -> tuple[str, list[str]]:
        text = text.strip()
        if not text.startswith("/"):
            return "", []
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        if not parts:
            return "", []
        cmd = parts[0].lower()
        # multi-word commands: /recon plan, /skills list, /subagents run
        if len(parts) >= 2:
            two = f"{cmd} {parts[1].lower()}"
            if two in self.handlers:
                return two, parts[2:]
        return cmd, parts[1:]

    def route(self, text: str) -> str | None:
        cmd, args = self.parse(text)
        if not cmd:
            return None
        # Bare `/` (atau `/` + spasi): bukan command. Di REPL interaktif ini sudah
        # ditangkap jadi menu; di mode plain/gateway jangan jadi "Unknown command `/`"
        # yang bikin bingung -- arahkan ke daftar, bukan error.
        if cmd == "/":
            return self.help_text()
        handler = self.handlers.get(cmd)
        if handler:
            return handler(args)
        return _unknown_command_message(cmd)

    def help_text(self, topic: str | None = None) -> str:
        """`/help` palette · `/help full` semua · `/help <kategori>` kategori itu.

        Bug lama (ditemukan saat UX pass): cabang `cat` di sini TIDAK PERNAH
        reachable -- `compact` selalu True untuk topic selain full/all/verbose,
        dan `cat` di-nol-kan begitu `compact` True. Jadi `/help setup` diam-diam
        menampilkan compact biasa. Sekarang topic non-kata-kunci diperlakukan
        sebagai nama kategori (registry memberi fallback + daftar valid bila
        kategori tidak ada).
        """
        t = (topic or "").strip().lower()
        if not t:
            return self.registry.help_text(compact=True)
        if t in {"full", "all", "verbose"}:
            return self.registry.help_text(compact=False)
        return self.registry.help_text(t, compact=False)

    def complete(self, partial: str) -> list[str]:
        return self.registry.complete(partial)


def build_slash_router(orch: Any) -> SlashRouter:
    """Wire orchestrator methods into slash handlers."""
    from wth_ai.cli.handlers.config_handler import handle_config
    from wth_ai.cli.handlers.platform_handler import handle_cron, handle_gateway, handle_memory, handle_mode
    from wth_ai.cli.handlers.recon_handler import handle_recon
    from wth_ai.cli.handlers.setup_handler import (
        apply_target_from_text,
        handle_model,
        handle_profile,
        handle_provider,
        handle_target,
        handle_ui,
    )
    from wth_ai.cli.handlers.skills_handler import handle_skills
    from wth_ai.cli.handlers.subagents_handler import handle_subagents
    from wth_ai.cli.handlers.tools_handler import handle_tools

    def _skills(args: list[str]) -> str:
        return handle_skills(orch, args)

    def _subagents(args: list[str]) -> str:
        return handle_subagents(orch, args)

    def _config(args: list[str]) -> str:
        return handle_config(orch, args)

    def _recon(args: list[str]) -> str:
        return handle_recon(orch, args)

    def _tools(args: list[str]) -> str:
        return handle_tools(orch, args)

    def _graph(args: list[str]) -> str:
        if not orch.asset_graph.nodes:
            return "Graph kosong. Jalankan `/recon` dulu."
        if args:
            q = " ".join(args).lower()
            nodes = [n for n in orch.asset_graph.nodes.values() if q in str(n.label).lower() or q in str(n.node_type).lower()]
            if not nodes:
                return f"No graph nodes match '{q}'"
            lines = [f"## Graph filter: {q}", ""]
            for n in nodes[:30]:
                lines.append(f"- [{n.node_type}] {n.label}")
            return "\n".join(lines)
        return orch.asset_graph.export_for_planner(limit=60)

    def _findings(_args: list[str]) -> str:
        rows = list(orch.findings_db.findings.values())
        if not rows:
            return "No findings yet."
        lines = ["## Findings", ""]
        for f in rows[:25]:
            lines.append(f"- `{f.id}` **{f.severity}** {f.title} ({f.state.value})")
        return "\n".join(lines)

    def _hypotheses(_args: list[str]) -> str:
        rows = list(orch.findings_db.hypotheses.values())
        if not rows:
            return "No hypotheses yet."
        lines = ["## Hypotheses", ""]
        for h in rows[:25]:
            lines.append(f"- {h.title}: {h.rationale[:80]}")
        return "\n".join(lines)

    def _mcp(args: list[str]) -> str:
        if not orch.config.mcp.enabled:
            return "MCP disabled in config."
        sub = args[0] if args else "list"
        if sub == "status":
            return f"MCP servers: {len(orch.config.mcp.servers)} configured"
        if sub == "tools":
            tools = orch.mcp.list_tools() if hasattr(orch.mcp, "list_tools") else []
            return "\n".join(tools[:40]) or "No MCP tools."
        return "\n".join(f"- {s.get('name', s)}" for s in orch.config.mcp.servers) or "No MCP servers."

    def _help(args: list[str]) -> str:
        # UX pass: logika topic dipusatkan di satu fungsi (sebelumnya tiga salinan
        # dengan bug yang sama -- `/help <kategori>` selalu jatuh ke compact).
        from wth_ai.cli.slash_router import _render_help

        return _render_help(args[0] if args else None)

    def _status(_args: list[str]) -> str:
        return orch._status_summary("WTH-AI Status")

    def _resume(args: list[str]) -> str:
        return orch.resume(" ".join(args) if args else None)

    def _scope(args: list[str]) -> str:
        if args and args[0] == "set":
            return orch._handle_scope(args)
        if args and "=" in args[0]:
            return orch._handle_scope(["set"] + args)
        return orch._handle_scope(args)

    handlers: dict[str, Handler] = {
        "/help": _help,
        # WU-118 (PRD-002 FR#1): merged from the separate literal if/elif
        # block that used to live in orchestrator.handle_input() -- these
        # /-prefixed forms are now regular SlashRouter entries like every
        # other command, dispatched through the SAME single mechanism.
        # Bare-word (no `/`) fast-path for quit/exit/stop is UNCHANGED and
        # still lives in handle_input() (SlashRouter.parse() structurally
        # requires a `/` prefix, so the bare forms need a separate check).
        "/quit": lambda _a: orch.request_quit(),
        "/exit": lambda _a: orch.request_quit(),
        "/stop": lambda _a: orch.supervisor.stop(),
        "/new-session": lambda _a: f"Session baru: {orch.new_session()}",
        "/resume": _resume,
        "/doctor": lambda _a: orch.doctor.full_report(),
        "/status": _status,
        "/sessions": lambda _a: orch._list_sessions(),
        "/target": lambda a: handle_target(orch, a),
        "/scope": _scope,
        "/permission": orch._handle_permission,
        "/computer": orch._handle_computer,
        "/research": orch._handle_research,
        "/jobs": orch._handle_jobs,
        "/eval": orch._handle_eval,
        "/usage": orch._handle_usage,
        "/next": orch._handle_next,
        "/payloads": orch._handle_payloads,
        "/autopilot": orch._handle_autopilot,
        "/eval act": orch._handle_eval_act,
        "/yolo": lambda args: orch._handle_permission(["yolo"]),
        "/model": lambda a: handle_model(orch, a),
        "/provider": lambda a: handle_provider(orch, a),
        "/profile": lambda a: handle_profile(orch, a),
        "/ui": lambda a: handle_ui(orch, a),
        "/recon": _recon,
        "/recon plan": lambda a: handle_recon(orch, ["plan"] + a),
        "/recon show": lambda a: handle_recon(orch, ["show"] + a),
        "/recon report": lambda a: handle_recon(orch, ["report"] + a),
        "/recon watch": lambda a: handle_recon(orch, ["watch"] + a),
        "/recon diff": lambda a: handle_recon(orch, ["diff"] + a),
        "/recon changes": lambda a: handle_recon(orch, ["changes"] + a),
        "/recon quick": lambda a: handle_recon(orch, ["quick"] + a),
        "/plan": lambda a: orch._assess_loop("/plan " + " ".join(a) if a else "/plan"),
        "/hunt": lambda a: orch._run_hunt(a),
        "/execute": lambda a: orch._execute_next(
            a[0] if a and a[0] != "--all" else None,
            run_all=bool(a and a[0] == "--all"),
        ),
        "/verify": lambda a: orch._verify(a[0] if a else None),
        "/report": lambda _a: orch._report(),
        "/findings": _findings,
        "/hypotheses": _hypotheses,
        "/approve": orch._handle_approve,
        "/graph": _graph,
        "/intel": orch._run_intel,
        "/skills": _skills,
        "/skill": lambda a: orch._show_skill(a[0] if a else ""),
        "/subagents": _subagents,
        "/config": _config,
        "/tools": _tools,
        "/mcp": _mcp,
        "/browser": orch._browser_probe,
        "/oob": orch._handle_oob,
        "/scan": _recon,
        "/gateway": lambda a: handle_gateway(orch, a),
        "/cron": lambda a: handle_cron(orch, a),
        "/memory": lambda a: handle_memory(orch, a),
        "/mode": lambda a: handle_mode(orch, a),
        "/engage": lambda a: orch._run_hunt(a) if a and a[0] == "start" else "Usage: /engage start",
    }
    return SlashRouter(handlers=handlers)
