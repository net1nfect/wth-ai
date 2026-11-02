"""Shared bot command service used by Telegram/Discord adapters.

Keeps command logic out of the platform daemons so behavior + permissions stay
consistent. Job-related commands use the minimal approval registry until the
full Job Manager lands in Fase 4.
"""

from __future__ import annotations

from typing import Any

from wth_ai.approvals import ApprovalRegistry
from wth_ai.config import WTHConfig
from wth_ai.integrations.base import command_allowed

# Commands recognized by the service (canonical names, no leading slash).
COMMANDS: tuple[str, ...] = (
    "status", "jobs", "approve", "deny", "stop", "report", "settings", "help",
)

HELP_TEXT = (
    "WTH-AI commands:\n"
    "- status — agent/integration status\n"
    "- jobs — pending approvals\n"
    "- approve <id> — approve a pending request\n"
    "- deny <id> — deny a pending request\n"
    "- stop <id> — request stop for a job\n"
    "- report <id> — locate a job report\n"
    "- settings — integration settings summary\n"
    "- help — this message"
)


class BotCommandService:
    """Resolve bot commands to text responses, enforcing role permissions."""

    def __init__(
        self,
        config: WTHConfig,
        *,
        approvals: ApprovalRegistry | None = None,
        jobs: Any = None,
    ) -> None:
        self.config = config
        self.approvals = approvals or ApprovalRegistry()
        self.jobs = jobs  # optional JobManager (Fase 4); falls back to approvals

    def handle(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        role: str = "owner",
        enforce_role: bool = False,
    ) -> str:
        args = args or []
        command = command.lstrip("/").lower()
        if command not in COMMANDS:
            return f"Unknown command '{command}'. Try 'help'."
        if enforce_role and not command_allowed(self.config.integrations.discord.role_permissions, role, command):
            return f"Permission denied: role '{role}' cannot run '{command}'."
        method = getattr(self, f"_cmd_{command}")
        return method(args)

    # --- command implementations ---

    def _cmd_help(self, args: list[str]) -> str:
        return HELP_TEXT

    def _cmd_status(self, args: list[str]) -> str:
        ig = self.config.integrations
        enabled = [n for n, c in (("telegram", ig.telegram), ("discord", ig.discord), ("webhook", ig.webhook)) if c.enabled]
        pending = len(self.approvals.list_pending())
        lines = [
            "WTH-AI status",
            f"- integrations enabled: {', '.join(enabled) or 'none'}",
            f"- pending approvals: {pending}",
        ]
        if self.jobs is not None:
            s = self.jobs.summary()
            lines.append(f"- jobs: {s['total']} total, {s['queued']} queued")
        return "\n".join(lines)

    def _cmd_jobs(self, args: list[str]) -> str:
        # Fase 4: read real jobs when a JobManager is wired; else show approvals.
        if self.jobs is not None:
            jobs = self.jobs.list_jobs(limit=20)
            if not jobs:
                return "No jobs."
            lines = ["Jobs:"]
            for j in jobs:
                lines.append(f"- {j.job_id} [{j.status}] {j.type} target={j.target or '-'}")
            return "\n".join(lines)
        pending = self.approvals.list_pending()
        if not pending:
            return "No pending approvals."
        lines = ["Pending approvals:"]
        for r in pending:
            lines.append(f"- {r['approval_id']} job={r['job_id']} action={r['action']} risk={r['risk']}")
        return "\n".join(lines)

    def _cmd_approve(self, args: list[str]) -> str:
        if not args:
            return "Usage: approve <approval_id>"
        rec = self.approvals.approve(args[0])
        if rec is None:
            return f"Approval '{args[0]}' not found."
        return f"Approval {rec['approval_id']} -> {rec['status']}"

    def _cmd_deny(self, args: list[str]) -> str:
        if not args:
            return "Usage: deny <approval_id>"
        rec = self.approvals.deny(args[0])
        if rec is None:
            return f"Approval '{args[0]}' not found."
        return f"Approval {rec['approval_id']} -> {rec['status']}"

    def _cmd_stop(self, args: list[str]) -> str:
        if not args:
            return "Usage: stop <job_id>"
        if self.jobs is not None:
            j = self.jobs.stop_job(args[0])
            return f"{args[0]} -> {j.status}" if j else f"{args[0]}: not found"
        return f"Stop requested for job '{args[0]}' (job manager not wired)."

    def _cmd_report(self, args: list[str]) -> str:
        if not args:
            return "Usage: report <job_id>"
        if self.jobs is not None:
            j = self.jobs.get_job(args[0])
            if j is None:
                return f"{args[0]}: not found"
            if j.result:
                return f"Job {j.job_id} result:\n{j.result}"
            return f"Job {j.job_id} status={j.status} (no result yet)"
        from wth_ai.workspace import wth_home
        return f"Reports directory: {wth_home() / 'reports'} (job '{args[0]}')."

    def _cmd_settings(self, args: list[str]) -> str:
        ig = self.config.integrations
        return (
            "Integration settings\n"
            f"- telegram: {'on' if ig.telegram.enabled else 'off'} "
            f"({len(ig.telegram.allowed_chat_ids)} chats)\n"
            f"- discord: {'on' if ig.discord.enabled else 'off'} "
            f"({len(ig.discord.allowed_channel_ids)} channels)\n"
            f"- webhook: {'on' if ig.webhook.enabled else 'off'}"
        )
