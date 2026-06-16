"""Slash command handler for WTH-AI interactive CLI."""

from __future__ import annotations

import shlex


class SlashCommandHandler:
    COMMANDS = [
        "/help", "/status", "/scope", "/permission", "/plan", "/reason", "/testplan", "/recon", "/hunt", "/graph",
        "/doctor", "/intel", "/mcp", "/tools", "/static", "/dynamic", "/execute", "/findings", "/verify", "/report",
        "/skills", "/skill", "/browser", "/hypotheses", "/triage", "/approve", "/resume",
        "/sessions", "/new-session", "/stop", "/patch", "/retest",
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
`/scope` · `/scope set target=domain.com authorized=yes accounts=userA:pass,userB:pass`
`/permission read_only|standard|elevated|full_lab`
`/status` · `/new-session` · `/resume`

## Assessment
`/plan` · `/reason` — cognition engine: explainable risk-ranked plan (offline)
`/testplan` — module-bound, permission-gated, budgeted non-destructive test queue
`/recon [target] [--fast]` · `/graph` · `/hunt` — graph-driven expert loop
`/doctor` · `/intel` · `/mcp` · `/tools` · `/tools install [category|all]`
`/execute` · `/execute --all` · `/triage` — correlate/dedup/false-positive filter · `/verify` · `/report`
`/browser https://target.com` — nodriver + Cloudflare bypass

## Skills (118+ HackSkills)
`/skills` · `/skills ctf` · `/skill xss-cross-site-scripting`

## Safety
`/approve <task_id>` · `/stop`

## Chat
Ketik apa saja — halo, tanya kemampuan, diskusi bug, dll.

## Keyboard
↑↓ history · Tab autocomplete · Ctrl+L clear · Ctrl+D exit
""".strip()
