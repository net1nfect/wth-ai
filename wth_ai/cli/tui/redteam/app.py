"""WTH-AI TUI red team — tampilan terminal bergaya coding agent.

Bukan dashboard: ini percakapan. Di kiri transkrip operasi, di kanan temuan dan
antrean, di bawah input perintah. Mode agen di header, diganti dengan tab.

Aturan tampilan yang dipegang di sini:

1. Tidak ada angka palsu — semua nilai dari state aplikasi.
2. Baris demo berlabel ``[demo]`` dan ditutup satu kalimat jujur.
3. Penolakan ditampilkan, bukan disembunyikan.
4. Header adalah satu-satunya tempat status.
"""

from __future__ import annotations

import asyncio
import shlex

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, RichLog, Static

from wth_ai import __version__
from wth_ai.cli.tui.redteam import banner as banner_mod
from wth_ai.cli.tui.redteam.agent_view import agent_summary
from wth_ai.cli.tui.redteam.mcp_view import mcp_summary
from wth_ai.cli.tui.redteam.skills_view import skill_fragment, skill_summary
from wth_ai.cli.tui.redteam.theme_css import redteam_css
from wth_ai.cli.ui.theme import THEME
from wth_ai.modes import BY_KEY, Cap, capability_header, check, matrix_rows, next_mode

DEMO_STEPS: tuple[tuple[int, str], ...] = (
    (0, "[b]scope[/b] 172.28.0.0/16 · red_team · authorized=yes"),
    (1, "[b]recon[/b] 42 endpoint, 7 parameter, 3 teknologi"),
    (2, "[b]queue[/b] 12 permukaan masuk antrean"),
    (3, "[b]probe[/b] GET /api/v1/orders?id=1001 → 200"),
    (4, "[b]diff[/b] identitas B membaca objek A: status 200 (seharusnya 403)"),
    (5, "[b]control[/b] objek milik sendiri → 403 (kontrol negatif valid)"),
    (6, "[b]replay[/b] 5/5 konsisten · median 44 ms"),
    (7, "[b]prior-art[/b] tidak ada CVE cocok di KB 2026-11-03"),
    (8, "[b]verdict[/b] CONFIRMED-NOVEL (BOLA pada /api/v1/orders)"),
    (9, "[b]disclosure[/b] advisory · cve-request · mitigation · retest"),
)

DEMO_DISCLAIMER = (
    "[dim]\\[demo] selesai — ini tata letak, bukan hasil uji nyata[/dim]"
)


class WthApp(App[None]):
    """TUI red team: transkrip + tabel temuan + antrean, mode di header."""

    CSS = redteam_css()
    TITLE = "WTH-AI"

    BINDINGS = [
        Binding("tab", "next_mode", "mode", show=True, priority=True),
        Binding("shift+tab", "prev_mode", "prev", show=False, priority=True),
        Binding("ctrl+k", "kill", "kill", show=True, priority=True),
        Binding("ctrl+l", "clear_log", "bersih", show=False),
        Binding("ctrl+s", "skills", "skills", show=True),
        Binding("ctrl+t", "mcp", "mcp", show=True),
        Binding("f1", "help", "bantuan", show=True),
        Binding("ctrl+q", "quit", "keluar", show=True),
    ]

    def __init__(self, demo: bool = False, target: str = "", cidr: str = "",
                 mode: str = "plan", destructive: bool = False) -> None:
        super().__init__()
        self.mode_key = mode if mode in BY_KEY else "plan"
        self.target = target
        self.cidr = cidr
        self.demo = demo
        self.allow_destructive = destructive
        self.queue_len = 0
        self.findings: list[dict[str, str]] = []
        self.running = False
        self.active_skill = ""
        self._demo_task: asyncio.Task | None = None

    # --- layout ----------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="rt-hdr")
        with Horizontal(id="rt-body"):
            with Vertical(id="rt-left"):
                yield RichLog(id="rt-log", wrap=True, markup=True, max_lines=4000)
            with Vertical(id="rt-right"):
                yield Static("TEMUAN", id="rt-rtitle")
                yield DataTable(id="rt-findings", zebra_stripes=True,
                                cursor_type="row")
                yield Static("ANTREAN · 0", id="rt-qtitle")
                yield Static(id="rt-queue")
        yield Input(placeholder="perintah… ketik /help", id="rt-prompt")
        yield Static(id="rt-hints")

    def on_mount(self) -> None:
        table = self.query_one("#rt-findings", DataTable)
        table.add_columns("id", "label", "sev", "state")
        self._log(banner_mod.banner(self.size.width, version=__version__))
        self._log("")
        self._refresh_hdr()
        self._refresh_hints()
        self.query_one("#rt-prompt", Input).focus()
        if self.demo:
            self._log("[dim]mode demo aktif — semua baris diberi label \\[demo][/dim]\n")
            self._demo_task = asyncio.create_task(self._run_demo())

    # --- helpers ---------------------------------------------------
    @property
    def mode(self):
        return BY_KEY[self.mode_key]

    def _log(self, text: str) -> None:
        self.query_one("#rt-log", RichLog).write(text)

    def _refresh_hdr(self) -> None:
        mode = self.mode
        live = f"[{THEME['error']}]● live[/{THEME['error']}]" if self.running else "[dim]○ idle[/dim]"
        target = self.target or "[dim]belum diset[/dim]"
        cidr = self.cidr or "[dim]—[/dim]"
        self.query_one("#rt-hdr", Static).update(
            f"[{mode.color}]{mode.glyph} {mode.label}[/{mode.color}] "
            f"[dim]L{mode.autonomy}[/dim]"
            f"   target [b]{target}[/b]   scope [b]{cidr}[/b]"
            f"   temuan [b]{len(self.findings)}[/b]"
            f"   antrean [b]{self.queue_len}[/b]   {live}"
        )

    def _refresh_hints(self) -> None:
        mode = self.mode
        confirm = "konfirmasi" if mode.asks else f"[{THEME['error']}]TANPA konfirmasi[/{THEME['error']}]"
        self.query_one("#rt-hints", Static).update(
            f"[dim]tab[/dim] mode · [{THEME['error']}]ctrl+k[/{THEME['error']}] kill · "
            f"[dim]ctrl+s[/dim] skills · [dim]ctrl+t[/dim] mcp · "
            f"[dim]f1[/dim] help · [dim]ctrl+q[/dim] keluar      "
            f"[dim]mode ini:[/dim] {confirm} · {mode.blurb}"
        )

    def _refresh_queue(self) -> None:
        self.query_one("#rt-qtitle", Static).update(f"ANTREAN · {self.queue_len}")
        if self.queue_len == 0:
            body = "[dim]kosong[/dim]"
        else:
            body = "\n".join(f"· permukaan {i + 1}"
                             for i in range(min(self.queue_len, 8)))
        self.query_one("#rt-queue", Static).update(body)

    def _add_finding(self, fid: str, label: str, sev: str, state: str) -> None:
        self.findings.append({"id": fid, "label": label, "sev": sev, "state": state})
        self.query_one("#rt-findings", DataTable).add_row(fid, label, sev, state)
        self._refresh_hdr()

    def _set_mode(self, key: str) -> None:
        if key not in BY_KEY:
            self._log(f"[{THEME['error']}]mode tidak dikenal: {key}[/{THEME['error']}]")
            return
        self.mode_key = key
        mode = BY_KEY[key]
        self._refresh_hdr()
        self._refresh_hints()
        self._log(f"[{mode.color}]{mode.glyph} mode → {mode.label}[/{mode.color}] "
                  f"[dim]{mode.blurb}[/dim]")

    # --- actions ---------------------------------------------------
    def action_next_mode(self) -> None:
        self._set_mode(next_mode(self.mode_key, 1))

    def action_prev_mode(self) -> None:
        self._set_mode(next_mode(self.mode_key, -1))

    def action_clear_log(self) -> None:
        self.query_one("#rt-log", RichLog).clear()
        self._log(banner_mod.banner(self.size.width, version=__version__))

    def action_kill(self) -> None:
        from wth_ai.killswitch import KILL_SWITCH, request_kill

        KILL_SWITCH.activate()
        request_kill("tui")
        self.running = False
        if self._demo_task and not self._demo_task.done():
            self._demo_task.cancel()
        self._log(f"[bold {THEME['error']}]KILL[/bold {THEME['error']}] semua worker berhenti (≤2 s) · "
                  "flag ditulis ke $WTH_HOME/runtime/KILL")
        self._refresh_hdr()

    def action_skills(self) -> None:
        self._log(skill_summary())

    def action_agents(self) -> None:
        try:
            self._log(agent_summary())
        except Exception as exc:  # noqa: BLE001 - panel must never crash the UI
            self._log(f"[{THEME['warn']}]agen[/{THEME['warn']}] tidak bisa dibaca: {exc}")

    def action_mcp(self) -> None:
        try:
            self._log(mcp_summary())
        except Exception as exc:  # noqa: BLE001 - status must never crash the UI
            self._log(f"[{THEME['warn']}]mcp[/{THEME['warn']}] tidak bisa dibaca: {exc}")

    def action_help(self) -> None:
        self._log(
            "[b]perintah[/b]\n"
            "  /mode <plan|build|auto|scout|strike|chain|scribe>\n"
            "  /modes              matrix kapabilitas\n"
            "  /scope <target> --cidr <C>\n"
            "  /run <recon|probe|exploit|chain|novel|mutate>\n"
            "  /egress-test        uji pagar keluar (harus DENIED)\n"
            "  /skill list | use <nama> | reload\n"
            "  /mcp                status server MCP\n"
            "  /agents             graf agen (read-only)\n"
            "  /demo on|off | /kill | /clear | /banner | /quit"
        )

    # --- input -----------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#rt-prompt", Input).value = ""
        if not text:
            return
        if not text.startswith("/"):
            self._log(f"[dim]›[/dim] {text}")
            self._log("[dim]perintah bebas belum dijalankan di TUI; gunakan /run[/dim]")
            return
        self._log(f"[dim]›[/dim] [b]{text}[/b]")
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        if not parts:
            return
        cmd, *args = parts
        cmd = cmd.lstrip("/").lower()

        if cmd in {"quit", "q", "exit"}:
            self.exit()
        elif cmd in {"help", "?"}:
            self.action_help()
        elif cmd == "mode":
            self._set_mode(args[0] if args else "")
        elif cmd == "modes":
            self._show_matrix()
        elif cmd == "scope":
            self._do_scope(args)
        elif cmd == "run":
            self._do_run(args[0] if args else "")
        elif cmd == "egress-test":
            self._do_egress_test()
        elif cmd == "kill":
            self.action_kill()
        elif cmd == "clear":
            self.action_clear_log()
        elif cmd == "banner":
            self._log(banner_mod.banner(self.size.width, version=__version__))
        elif cmd == "skill":
            self._do_skill(args)
        elif cmd == "skills":
            self._log(skill_summary())
        elif cmd == "mcp":
            self.action_mcp()
        elif cmd == "agents":
            self.action_agents()
        elif cmd == "demo":
            self._do_demo(args)
        else:
            self._log(f"[{THEME['error']}]perintah tidak dikenal: /{cmd}[/{THEME['error']}]")

    def _show_matrix(self) -> None:
        header = capability_header()
        rows = matrix_rows()
        widths = [max(len(str(row[i])) for row in (header, *rows))
                  for i in range(len(header))]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        self._log("[b]" + fmt.format(*header) + "[/b]")
        for row in rows:
            self._log(fmt.format(*row))

    # --- command handlers ------------------------------------------
    def _do_scope(self, args: list[str]) -> None:
        if not args:
            self._log(f"[{THEME['warn']}]pakai: /scope 172.28.0.10 --cidr 172.28.0.0/16[/{THEME['warn']}]")
            return
        target = args[0]
        cidr = ""
        if "--cidr" in args:
            index = args.index("--cidr")
            if index + 1 < len(args):
                cidr = args[index + 1]
        if not cidr:
            self._log(f"[{THEME['error']}]ditolak:[/{THEME['error']}] scope tanpa CIDR tidak boleh — "
                      "pagar egress butuh allowlist")
            return
        from wth_ai.scope_card import ScopeCard, ScopeCardError
        from wth_ai.workspace import wth_home

        card = ScopeCard(target=target, cidr=[cidr], authorized=True,
                         mode=self.mode_key, destructive=self.allow_destructive)
        try:
            path = card.write(wth_home() / "scope_card.json")
        except ScopeCardError as exc:
            self._log(f"[{THEME['error']}]ditolak:[/{THEME['error']}] {exc}")
            return
        self.target, self.cidr = target, cidr
        self._refresh_hdr()
        self._log(f"[{THEME['accent']}]scope diset[/{THEME['accent']}] target={target} cidr={cidr} "
                  f"authorized=yes · ditulis ke {path}")

    def _do_run(self, cap_name: str) -> None:
        try:
            cap = Cap(cap_name)
        except ValueError:
            self._log(f"[{THEME['error']}]kapabilitas tidak dikenal: {cap_name}[/{THEME['error']}]")
            return
        mode = self.mode
        if not check(mode, cap):
            self._log(f"[{THEME['error']}]DITOLAK[/{THEME['error']}] mode '{mode.label}' tidak punya "
                      f"kapabilitas '{cap.value}'")
            return
        if cap is Cap.MUTATE and not self.allow_destructive:
            self._log(f"[{THEME['error']}]DITOLAK[/{THEME['error']}] mutate butuh --destructive "
                      "(lab saja) · destructive=false")
            return
        self.running = True
        self._refresh_hdr()
        self.queue_len = max(self.queue_len, 1)
        self._refresh_queue()
        self._log(f"[{THEME['accent']}]{cap.value}[/{THEME['accent']}] siap di mode {mode.label} "
                  "[dim](loop nyata dijalankan `wth-ai scan`)[/dim]")

    def _do_egress_test(self) -> None:
        from wth_ai.netguard import EgressDenied, EgressGuard

        guard = EgressGuard.from_patterns([self.cidr] if self.cidr else [])
        try:
            guard.check("1.1.1.1")
        except EgressDenied as exc:
            self._log(f"[{THEME['accent']}]EGRESS DENIED[/{THEME['accent']}] {exc} "
                      "[dim](tercatat di audit.log)[/dim]")
            return
        self._log(f"[{THEME['error']}]BOCOR[/{THEME['error']}] 1.1.1.1 LOLOS dari allowlist — "
                  "jangan merekam demo ini")

    def _do_skill(self, args: list[str]) -> None:
        if not args or args[0] == "list":
            self._log(skill_summary())
            return
        if args[0] == "reload":
            self._log(skill_summary())
            return
        if args[0] == "use" and len(args) > 1:
            fragment = skill_fragment(args[1])
            if not fragment:
                self._log(f"[{THEME['error']}]skill '{args[1]}' tidak ada[/{THEME['error']}]")
                return
            self.active_skill = args[1]
            self._log(f"[{THEME['accent']}]skill aktif[/{THEME['accent']}] {args[1]} "
                      f"({len(fragment.splitlines())} baris) → dimasukkan ke prompt")
            return
        self._log(f"[{THEME['warn']}]pakai: /skill list | use <nama> | reload[/{THEME['warn']}]")

    def _do_demo(self, args: list[str]) -> None:
        want = (args[0].lower() if args else "on") in {"on", "1", "true"}
        if want and not self._demo_task:
            self._demo_task = asyncio.create_task(self._run_demo())
            self._log("[dim]demo dijalankan[/dim]")
        elif not want and self._demo_task:
            self._demo_task.cancel()
            self._demo_task = None
            self._log("[dim]demo dihentikan[/dim]")

    # --- demo ------------------------------------------------------
    async def _run_demo(self) -> None:
        """Aliran peristiwa untuk memperlihatkan tata letak. Semua [demo]."""
        try:
            self.running = True
            self._refresh_hdr()
            for _, line in DEMO_STEPS:
                await asyncio.sleep(0.55)
                self._log(f"[dim]\\[demo][/dim] {line}")
                if "queue" in line:
                    self.queue_len = 12
                    self._refresh_queue()
                    self._refresh_hdr()
                if "verdict" in line:
                    self._add_finding("F-001", "BOLA /orders", "HIGH",
                                      "CONFIRMED-NOVEL")
                    self.queue_len = 0
                    self._refresh_queue()
                    self._refresh_hdr()
            self.running = False
            self._refresh_hdr()
            self._log(DEMO_DISCLAIMER)
        except asyncio.CancelledError:
            self.running = False
            self._refresh_hdr()
            raise

    # --- bukti visual ---------------------------------------------
    def save_screenshot(self, path: str = "out/tui.svg") -> str:
        """Simpan SVG tampilan saat ini. Memakai `App.export_screenshot()`."""
        from pathlib import Path

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.export_screenshot(), encoding="utf-8")
        return str(out)


def run(demo: bool = False, target: str = "", cidr: str = "",
        mode: str = "plan", destructive: bool = False) -> None:
    WthApp(demo=demo, target=target, cidr=cidr, mode=mode,
           destructive=destructive).run()
