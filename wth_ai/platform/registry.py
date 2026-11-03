"""Unified platform command registry for autocomplete, palette, and /help."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlatformCommand:
    name: str
    usage: str
    description: str
    category: str
    aliases: tuple[str, ...] = ()


#: Category order + one-line human description. The palette used to show bare
#: category names ("meta", "hunt", ...) with a "N command" hint, so a new operator
#: could not tell where to start or what a group was for. Order is the workflow
#: order; the description explains the group. `START_HERE` is the recommended path
#: surfaced on the palette's first level.
CATEGORY_ORDER: tuple[str, ...] = (
    "setup", "safety", "recon", "hunt", "intel", "tools", "skills", "subagents",
    "mcp", "session", "meta", "platform",
)

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "setup": "setel target, scope, permission, model",
    "safety": "preset izin & batas operasi",
    "recon": "petakan attack surface",
    "hunt": "rencana → eksekusi → verifikasi → laporan",
    "intel": "graph aset & riset publik",
    "tools": "tool, browser, callback center, doctor",
    "skills": "skill pack & aktivasi",
    "subagents": "worker terspesialisasi",
    "mcp": "server MCP eksternal",
    "session": "simpan / lanjutkan / keluar",
    "meta": "status, biaya, pengukuran",
    "platform": "gateway, cron, memory, mode",
}

#: Recommended first-run path, shown at the top of the palette so "/" is never a
#: dead-end of 60 near-identical rows.
START_HERE: tuple[tuple[str, str], ...] = (
    ("/target", "1. set target + otorisasi"),
    ("/scope", "2. perjelas in/out of scope"),
    ("/permission", "3. pilih mode izin"),
    ("/recon", "4. petakan attack surface"),
    ("/hunt", "5. jalankan engagement loop"),
)

SUBAGENT_SPECS: tuple[dict[str, str], ...] = (
    {"id": "recon", "desc": "Bounded reconnaissance"},
    {"id": "injection", "desc": "SQLi, CMD, SSTI analysis"},
    {"id": "xss", "desc": "XSS analysis"},
    {"id": "auth", "desc": "Authentication bypass"},
    {"id": "authz", "desc": "IDOR / BOLA"},
    {"id": "ssrf", "desc": "SSRF witness testing"},
    {"id": "infra", "desc": "Misconfig / info disclosure"},
    {"id": "ctf", "desc": "CTF challenge solver"},
    {"id": "reporter", "desc": "Final report builder"},
)


@dataclass
class PlatformRegistry:
    """Index slash commands and platform metadata."""

    commands: list[PlatformCommand] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.commands:
            self.commands = list(_default_commands())

    def all_slash_names(self) -> list[str]:
        names: list[str] = []
        for cmd in self.commands:
            names.append(cmd.name)
            names.extend(cmd.aliases)
        return sorted(set(names))

    @staticmethod
    def category_title(category: str) -> str:
        """Nama pendek untuk kolom label palette & menu ("MCP", "Skills")."""
        return {"mcp": "MCP"}.get(category or "", (category or "").title() or "Lainnya")

    def category_label(self, category: str) -> str:
        """Human label for a category: "recon · petakan attack surface"."""
        desc = CATEGORY_DESCRIPTIONS.get(category, "")
        return f"{category} · {desc}" if desc else category

    def suggestions(self, text: str, limit: int = 5) -> list[str]:
        """Closest command names for a mistyped/unknown `/command`.

        Two cheap signals, strongest first: prefix, then substring, then
        subsequence (`/rcn` -> `/recon`). Returns names only, ranked, so the
        caller can render them as a short list rather than a wall of rows.
        """
        q = (text or "").strip().lstrip("/").lower()
        if not q:
            return []
        names = [c.name for c in self.commands]
        prefix = [n for n in names if n.lstrip("/").lower().startswith(q)]
        substring = [n for n in names if q in n.lstrip("/").lower() and n not in prefix]
        subseq = [n for n in names if _subsequence_local(q, n.lstrip("/").lower())
                  and n not in prefix and n not in substring]
        return (prefix + substring + subseq)[:limit]

    def search(self, query: str, limit: int = 20) -> list[PlatformCommand]:
        q = query.lower().lstrip("/")
        if not q:
            return self.commands[:limit]
        hits: list[PlatformCommand] = []
        for cmd in self.commands:
            hay = f"{cmd.name} {cmd.usage} {cmd.description} {cmd.category}".lower()
            if q in hay or any(q in a for a in cmd.aliases):
                hits.append(cmd)
            if len(hits) >= limit:
                break
        return hits

    def complete(self, partial: str) -> list[str]:
        partial = partial.strip()
        if not partial.startswith("/"):
            return []
        token = partial.split()[0].lower()
        return [n for n in self.all_slash_names() if n.startswith(token) and n != token][:15]

    def help_text(self, category: str | None = None, *, compact: bool = True) -> str:
        """Palette (`compact`), satu kategori, atau daftar lengkap.

        Kategori tak dikenal TIDAK lagi diam-diam menampilkan halaman kosong:
        jatuh kembali ke palette + daftar kategori yang valid.
        """
        if compact and not category:
            return self._help_compact()
        present = {c.category for c in self.commands}
        if category and category not in present:
            order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
            valid = " · ".join(sorted(present, key=lambda c: order.get(c, 99)))
            return self._help_compact() + f"\n\n(kategori `{category}` tidak ada — pilihan: {valid})"
        lines = ["# WTH-AI Slash Commands", ""]
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        cats = sorted(present, key=lambda c: order.get(c, 99))
        for cat in cats:
            if category and cat != category:
                continue
            lines.append(f"## {self.category_label(cat)}")
            for cmd in self.commands:
                if cmd.category != cat:
                    continue
                lines.append(f"- `{cmd.name}` — {cmd.description}")
                lines.append(f"  usage: `{cmd.usage}`")
            lines.append("")
        lines.append("Natural language (no `/`) → supervisor chat.")
        lines.append("`/help full` — daftar lengkap · `/help <kategori>` — per kategori.")
        return "\n".join(lines)

    def _help_compact(self) -> str:
        """Palette alur-kerja: mulai di sini → kategori-kategori → tips.

        Dulu ini daftar tetap berisi sebagian command; sekarang tiap kategori
        menyebutkan JUMLAH command + deskripsi satu baris, dan blok "mulai di
        sini" menjawab pertanyaan pertama operator baru ("harus ngapain dulu?").
        """
        by_cat: dict[str, list[str]] = {}
        for cmd in self.commands:
            by_cat.setdefault(cmd.category, []).append(cmd.name)
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}

        lines = [
            f"WTH-AI · {len(self.commands)} command — `/help full` daftar lengkap · `/help <kategori>` per kategori",
            "",
            "▶ MULAI DI SINI",
        ]
        lines += [f"   {cmd:<14} {step}" for cmd, step in START_HERE]
        lines.append("")
        for cat in sorted(by_cat, key=lambda c: order.get(c, 99)):
            names = by_cat[cat]
            label = CATEGORY_DESCRIPTIONS.get(cat, "")
            head = f"{self.category_title(cat):<9} {label}".rstrip()
            lines.append(f"{head}  ({len(names)})")
            lines.append(f"   {' · '.join(names[:8])}" + (" …" if len(names) > 8 else ""))
        lines.append("")
        lines.append("Permission: read_only · standard · elevated (alias active_test) · full_lab · unrestricted")
        lines.append("Chat tanpa `/` → diskusi; `/` di mode interaktif → menu; Esc membatalkan menu.")
        return "\n".join(lines)

    def subagent_list_text(self) -> str:
        lines = ["# Subagents", ""]
        for spec in SUBAGENT_SPECS:
            lines.append(f"- **{spec['id']}** — {spec['desc']}")
        lines.append("\nRun: `/subagents run <id>`")
        return "\n".join(lines)


def _subsequence_local(needle: str, haystack: str) -> bool:
    """`needle` muncul di `haystack` berurutan (tidak harus berdampingan)."""
    if not needle:
        return False
    it = iter(haystack)
    return all(ch in it for ch in needle)


def _default_commands() -> list[PlatformCommand]:
    return [
        PlatformCommand("/help", "/help [topic]", "Show help", "meta"),
        PlatformCommand("/status", "/status", "Session status", "meta"),
        PlatformCommand("/doctor", "/doctor", "Health check", "meta"),
        PlatformCommand("/quit", "/quit", "Exit WTH-AI and stop active work", "meta", ("/exit",)),
        # Command yang sudah lama ada di SlashRouter.handlers tapi TIDAK pernah
        # didaftarkan di sini. Akibatnya: menu completion, panel /help, dan sweep
        # otomatis (yang menyusuri registry) tidak pernah melihatnya -- `/usage`,
        # `/next`, `/payloads`, `/autopilot`, `/eval act` lolos dari UAT pertama gua
        # karena alasan ini. Registry adalah daftar tampilan, tapi kalau ia tidak
        # lengkap, ketidakkonsistenan jadi senyap.
        PlatformCommand("/usage", "/usage", "Token & biaya yang tercatat", "meta"),
        PlatformCommand("/next", "/next", "Langkah uji berikutnya yang disusun AI", "hunt"),
        PlatformCommand("/jobs", "/jobs [target]", "Job store: rekam jejak recon/executor", "meta"),
        PlatformCommand("/payloads", "/payloads [id|gen <kelas> <tujuan>]", "Katalog payload (berbatas)", "hunt"),
        PlatformCommand("/autopilot", "/autopilot [langkah]", "Jalankan langkah uji berbatas otonom", "hunt"),
        PlatformCommand("/eval act", "/eval act", "Jalankan aksi self-improvement", "meta"),
        PlatformCommand("/eval", "/eval [run|history|act]", "Pengukuran self-improvement", "meta"),
        PlatformCommand("/research", "/research [search|fetch|notes] <q>", "Riset publik (bukan bukti target)", "intel"),
        PlatformCommand("/computer", "/computer [detect|screenshot|windows|click|type]", "Kendali desktop", "tools"),
        PlatformCommand("/scan", "/scan [target]", "Recon cepat satu perintah", "recon"),
        PlatformCommand("/yolo", "/yolo", "Preset tanpa konfirmasi (scope tetap wajib)", "safety"),
        PlatformCommand("/stop", "/stop", "Stop agent (stay in session)", "meta"),
        PlatformCommand("/sessions", "/sessions", "List saved sessions", "session"),
        PlatformCommand("/new-session", "/new-session", "Start new session", "session"),
        PlatformCommand("/resume", "/resume [id]", "Resume session", "session"),
        PlatformCommand("/target", "/target [url|domain|clear|show]", "Set assessment target", "setup"),
        PlatformCommand("/scope", "/scope [set key=val ...]", "Full scope (advanced)", "setup", ("/scope set",)),
        PlatformCommand("/permission", "/permission [read_only|standard|elevated|full_lab|unrestricted]", "Permission mode", "setup"),
        PlatformCommand("/model", "/model [list|use N|set role id|fix|show]", "Switch LLM models", "setup"),
        PlatformCommand("/provider", "/provider [show|set role type|list]", "Switch LLM provider", "setup"),
        PlatformCommand("/profile", "/profile [list|use name]", "Engagement profile preset", "setup"),
        PlatformCommand("/ui", "/ui [show|compact on|off|mode plain|tui]", "Terminal UI settings", "setup"),
        PlatformCommand("/config", "/config [show|set key=val]", "Raw config (advanced)", "setup"),
        PlatformCommand("/recon", "/recon [target] [--fast] [--profile NAME]", "Run recon", "recon", ("/scan",)),
        PlatformCommand("/recon plan", "/recon plan [target]", "Show recon plan only", "recon"),
        PlatformCommand("/recon show", "/recon show <job_id>", "Show recon job", "recon"),
        PlatformCommand("/recon report", "/recon report <job_id>", "Show recon report", "recon"),
        PlatformCommand("/recon watch", "/recon watch <target> [--interval 6h]", "Schedule periodic recon", "recon"),
        PlatformCommand("/recon diff", "/recon diff <job_a> <job_b>", "Compare two recon jobs", "recon"),
        PlatformCommand("/recon changes", "/recon changes [target]", "Changes vs prior recon", "recon"),
        PlatformCommand("/recon quick", "/recon quick <target>", "Fast budget recon", "recon"),
        PlatformCommand("/plan", "/plan [goal]", "Planner hunt plan", "hunt"),
        PlatformCommand("/hunt", "/hunt [goal]", "Full engagement loop", "hunt"),
        PlatformCommand("/execute", "/execute [task_id|--all]", "Execute queued tasks", "hunt"),
        PlatformCommand("/verify", "/verify [finding_id]", "Verify findings", "hunt"),
        PlatformCommand("/report", "/report", "Generate report", "hunt"),
        PlatformCommand("/findings", "/findings", "List findings", "hunt"),
        PlatformCommand("/hypotheses", "/hypotheses", "List hypotheses", "hunt"),
        PlatformCommand("/approve", "/approve <id>", "Approve task", "hunt"),
        PlatformCommand("/graph", "/graph [query]", "Asset graph", "intel"),
        PlatformCommand("/intel", "/intel [domain]", "Intel enrichment", "intel"),
        PlatformCommand("/skills", "/skills [list|search|install|use|doctor] [args]", "Skill packs", "skills"),
        PlatformCommand("/skill", "/skill <id>", "Show or activate skill", "skills"),
        PlatformCommand("/subagents", "/subagents [list|run|status] [args]", "Subagent control", "subagents"),
        PlatformCommand("/tools", "/tools [list|install|doctor|show|enable|disable|available|missing] [args]", "Tool registry", "tools"),
        PlatformCommand("/mcp", "/mcp [list|status|tools]", "MCP servers", "mcp"),
        PlatformCommand("/browser", "/browser <url>", "Browser probe", "tools"),
        PlatformCommand("/oob", "/oob [start|stop|new|list|hits] [args]", "Callback center: bukti blind SSRF/XXE/RCE", "tools"),
        PlatformCommand("/gateway", "/gateway status", "Gateway daemon health", "platform"),
        PlatformCommand("/cron", "/cron [list|add|remove] [args]", "Scheduled jobs", "platform"),
        PlatformCommand("/memory", "/memory [show|search|edit] [args]", "FTS + MEMORY.md", "platform"),
        PlatformCommand("/mode", "/mode [ctf|bounty|default]", "Engagement mode", "platform"),
        PlatformCommand("/engage", "/engage start", "Start engagement", "platform"),
    ]


_default_registry = PlatformRegistry()


def get_platform_registry() -> PlatformRegistry:
    return _default_registry
