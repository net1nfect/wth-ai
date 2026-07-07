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
        if compact and not category:
            return self._help_compact()
        lines = ["# WTH-AI Slash Commands", ""]
        cats = sorted({c.category for c in self.commands})
        for cat in cats:
            if category and cat != category:
                continue
            lines.append(f"## {cat.title()}")
            for cmd in self.commands:
                if cmd.category != cat:
                    continue
                lines.append(f"- `{cmd.name}` — {cmd.description}")
                lines.append(f"  usage: `{cmd.usage}`")
            lines.append("")
        lines.append("Natural language (no `/`) → supervisor chat.")
        lines.append("`/help full` — daftar lengkap per command.")
        return "\n".join(lines)

    def _help_compact(self) -> str:
        return "\n".join([
            "WTH-AI — ketik `/help full` untuk detail per command",
            "",
            "Setup     /target · /scope · /permission · /model · /provider · /ui · /config · /status",
            "Recon     /recon · /recon plan · /recon quick · /recon watch · /graph",
            "Hunt      /plan · /hunt · /execute · /verify · /report · /findings",
            "Tools     /tools · /doctor · /browser · /tools install",
            "Skills    /skills · /skill",
            "MCP       /mcp list · /mcp status",
            "Session   /new-session · /resume · /quit · /stop",
            "",
            "Permission: read_only · standard · elevated (alias active_test) · full_lab · unrestricted",
            "Chat tanpa `/` → diskusi; recon/hunt pakai slash di atas.",
        ])

    def subagent_list_text(self) -> str:
        lines = ["# Subagents", ""]
        for spec in SUBAGENT_SPECS:
            lines.append(f"- **{spec['id']}** — {spec['desc']}")
        lines.append("\nRun: `/subagents run <id>`")
        return "\n".join(lines)


def _default_commands() -> list[PlatformCommand]:
    return [
        PlatformCommand("/help", "/help [topic]", "Show help", "meta"),
        PlatformCommand("/status", "/status", "Session status", "meta"),
        PlatformCommand("/doctor", "/doctor", "Health check", "meta"),
        PlatformCommand("/quit", "/quit", "Exit WTH-AI and stop active work", "meta", ("/exit",)),
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
        PlatformCommand("/gateway", "/gateway status", "Gateway daemon health", "platform"),
        PlatformCommand("/cron", "/cron [list|add|remove] [args]", "Scheduled jobs", "platform"),
        PlatformCommand("/memory", "/memory [show|search|edit] [args]", "FTS + MEMORY.md", "platform"),
        PlatformCommand("/mode", "/mode [ctf|bounty|default]", "Engagement mode", "platform"),
        PlatformCommand("/engage", "/engage start", "Start engagement", "platform"),
    ]


_default_registry = PlatformRegistry()


def get_platform_registry() -> PlatformRegistry:
    return _default_registry
