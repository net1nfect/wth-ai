"""Slash command handler for WTH-AI interactive CLI."""

from __future__ import annotations

import shlex


class SlashCommandHandler:
    COMMANDS = [
        "/help", "/status", "/target", "/scope", "/model", "/provider", "/profile", "/ui",
        "/permission", "/plan", "/recon", "/hunt", "/graph",
        "/doctor", "/intel", "/mcp", "/tools", "/static", "/dynamic", "/execute", "/findings", "/verify", "/report",
        "/skills", "/skill", "/browser", "/hypotheses", "/approve", "/resume",
        "/sessions", "/new-session", "/quit", "/exit", "/stop", "/patch", "/retest", "/config", "/mode", "/gateway", "/cron", "/memory",
    ]

    def parse(self, text: str) -> tuple[str, list[str]]:
        text = text.strip()
        if not text.startswith("/"):
            return "chat", [text]
        parts = shlex.split(text)
        return parts[0].lower(), parts[1:]

    def help_text(self) -> str:
        return """
# WTH-AI Commands

## Setup
`/target example.com` · `/target https://host/path` · `/target clear`
`/model list` · `/model 4` · `/model set planner Hacker` · `/model set all cu/default`
`/provider show` · `/provider set all 9router`
`/profile list` · `/profile use bug-bounty`
`/permission read_only|standard|elevated|full_lab|unrestricted` (alias: `active_test` → elevated)
`/ui compact on|off` · `/ui mode plain|tui`
`/scope set target=domain.com authorized=yes` (advanced)
`/config show` · `/status` · `/new-session` · `/resume`

## Assessment
`/plan` · `/recon [target] [--fast]` · `/graph` · `/hunt` — graph-driven expert loop
`/doctor` · `/intel` · `/mcp` · `/tools` · `/tools install all` (Linux/macOS/Windows)
`/tools install recon.subdomain` · `/tools install all --no-sudo`
`/execute` · `/execute --all` · `/verify` · `/report`
`/browser https://target.com` — nodriver + Cloudflare bypass
`/mode ctf|bounty|default` · `/gateway status` · `/cron list` · `/memory show`

## Skills (118+ HackSkills)
`/skills` · `/skills ctf` · `/skill xss-cross-site-scripting`

## Safety
`/approve <task_id>` · `/stop` (hentikan agent) · `/quit` (keluar)

## Chat
Natural language untuk diskusi — setup & config gunakan slash di atas.

## Keyboard
↑↓ history · Tab autocomplete · Ctrl+L clear · Ctrl+D exit
""".strip()
