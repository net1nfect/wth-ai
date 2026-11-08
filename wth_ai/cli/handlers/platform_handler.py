"""Slash handlers for gateway, cron, memory, mode."""

from __future__ import annotations

from typing import Any


def handle_gateway(orch: Any, args: list[str]) -> str:
    sub = args[0] if args else "status"
    if sub == "stop":
        try:
            from wth_ai.gateway.background import is_gateway_running, stop_gateway

            if not is_gateway_running():
                return "Gateway: not running"
            stop_gateway()
            return "Gateway stopped."
        except Exception as exc:
            return f"Gateway stop error: {exc}"
    if sub != "status":
        return "Usage: `/gateway status` or `/gateway stop`"
    try:
        from wth_ai.gateway.background import gateway_status_info

        info = gateway_status_info()
        if not info.get("running"):
            return "Gateway: not running (`wth-ai gateway run --background`)"
        rpc = info.get("rpc", {})
        sess = rpc.get("sessions", {})
        worker = rpc.get("worker", {})
        return (
            f"## Gateway status\n\n"
            f"- pid: {info.get('pid')}\n"
            f"- socket: `{info.get('socket')}`\n"
            f"- log: `{info.get('log')}`\n"
            f"- sessions: {sess.get('count', 0)}/{sess.get('max', 128)}\n"
            f"- worker running: {worker.get('running', False)}\n"
        )
    except Exception as exc:
        return f"Gateway error: {exc}"


def handle_cron(orch: Any, args: list[str]) -> str:
    from wth_ai.cron.store import CronStore
    store = CronStore()
    if not args or args[0] == "list":
        entries = store.list()
        if not entries:
            return "No cron jobs. Example: `/cron add recon example.com every 6h`"
        lines = ["## Cron jobs", ""]
        for e in entries:
            lines.append(f"- `{e.cron_id}` {e.job_type} {e.target} — {e.schedule}")
        return "\n".join(lines)
    if args[0] == "add" and len(args) >= 4:
        job_type = args[1]
        target = args[2]
        schedule = " ".join(args[3:])
        entry = store.add(job_type, target, schedule)
        return f"Cron added: `{entry.cron_id}` {job_type} {target} ({schedule})"
    if args[0] == "remove" and len(args) >= 2:
        ok = store.remove(args[1])
        return "Removed." if ok else f"Unknown cron id: {args[1]}"
    if args[0] == "enable" and len(args) >= 2:
        ok = store.set_enabled(args[1], True)
        return "Cron enabled." if ok else f"Unknown cron id: {args[1]}"
    if args[0] == "disable" and len(args) >= 2:
        ok = store.set_enabled(args[1], False)
        return "Cron disabled." if ok else f"Unknown cron id: {args[1]}"
    return "Usage: /cron list | add <type> <target> every 6h | remove <id> | enable|disable <id>"


def handle_memory(orch: Any, args: list[str]) -> str:
    from wth_ai.memory import fts as fts_mod
    from wth_ai.memory.md_context import read_memory, read_user, write_memory, write_user
    from wth_ai.memory.action_memory import ActionMemory

    sub = args[0] if args else "show"
    if sub == "actions":
        am = ActionMemory(orch.state.session_id)
        block = am.context_block(limit=25)
        return block or "No action memory yet."
    if sub == "clear" and len(args) >= 2 and args[1] == "actions":
        am = ActionMemory(orch.state.session_id)
        am.records = []
        am.save()
        return "Action memory cleared."
    if sub == "search" and len(args) >= 2:
        q = " ".join(args[1:])
        idx = fts_mod.SessionFtsIndex()
        hits = idx.search(q)
        if not hits:
            return f"No memory hits for: {q}"
        lines = [f"## Memory search: {q}", ""]
        for h in hits:
            lines.append(f"- [{h['role']}] {h['snippet']} ({h['session_key']})")
        return "\n".join(lines)
    if sub == "show":
        return f"## USER.md\n\n{read_user() or '(empty)'}\n\n## MEMORY.md\n\n{read_memory() or '(empty)'}"
    if sub == "edit" and len(args) >= 3:
        which = args[1]
        text = " ".join(args[2:])
        if which == "memory":
            write_memory(text)
            return "MEMORY.md updated."
        if which == "user":
            write_user(text)
            return "USER.md updated."
        return "Use: /memory edit memory|user <text>"
    return "Usage: /memory show | search <q> | actions | clear actions | edit memory|user <text>"


def handle_mode(orch: Any, args: list[str]) -> str:
    from wth_ai.engagement.mode import EngagementMode, get_engagement_mode, set_engagement_mode
    if not args:
        agent_mode = getattr(getattr(orch.config, "agent", None), "mode", "hybrid")
        return f"Agent mode: `{agent_mode}` · Engagement mode: `{get_engagement_mode().value}`"
    mode = args[0].lower().replace("-", "_")
    if mode in {"hybrid", "chat", "security"}:
        orch.config.agent.mode = mode
        orch.config.save_user_config()
        return f"Agent mode set to `{mode}`"
    mapping = {"ctf": EngagementMode.CTF, "bounty": EngagementMode.BUG_BOUNTY, "bug_bounty": EngagementMode.BUG_BOUNTY, "default": EngagementMode.DEFAULT}
    if mode not in mapping:
        return "Usage: /mode hybrid|chat|security  OR  /mode ctf|bounty|default"
    set_engagement_mode(mapping[mode])
    return f"Engagement mode set to `{mapping[mode].value}`"
