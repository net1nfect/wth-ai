"""Notification adapter interface + shared helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from wth_ai.events import EVENT_NOTIFY_KEY, Event
from wth_ai.vault import is_vault_ref, mask_secret

# A transport hook: (recipient, text, token) -> bool. Injected in tests.
Sender = Callable[[str, str, str | None], bool]


def command_allowed(role_permissions: dict[str, list[str]], role: str, command: str) -> bool:
    """Return True if ROLE may run COMMAND given a role->permissions mapping."""
    perms = role_permissions.get(role, [])
    return "*" in perms or command in perms


# Highest privilege first.
_ROLE_PRIORITY = ("owner", "admin", "analyst", "viewer")


def resolve_discord_role(member_role_names: list[str], role_permissions: dict[str, list[str]]) -> str:
    """Map a member's Discord role names to the highest matching WTH role.

    Falls back to ``viewer`` (least privilege) when nothing matches.
    """
    lowered = {n.lower() for n in member_role_names}
    for role in _ROLE_PRIORITY:
        if role in role_permissions and role in lowered:
            return role
    return "viewer"


def _resolve_token(token_ref: str | None) -> str | None:
    """Resolve a ``vault:<name>`` token reference to its secret (or pass through)."""
    if not token_ref:
        return None
    if is_vault_ref(token_ref):
        from wth_ai.vault import SecretVault
        return SecretVault().resolve(token_ref)
    return token_ref


class NotificationAdapter(ABC):
    """Base for outbound notification adapters (Telegram/Discord/Webhook)."""

    name: str = "base"

    def __init__(self, cfg: Any, *, sender: Sender | None = None) -> None:
        self.cfg = cfg
        self._sender = sender

    # --- config-driven gating ---

    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "enabled", False))

    def token(self) -> str | None:
        return _resolve_token(getattr(self.cfg, "bot_token_ref", None))

    @abstractmethod
    def recipients(self) -> list[str]:
        ...

    def should_notify(self, event: Event) -> bool:
        key = EVENT_NOTIFY_KEY.get(event.type)
        if key is None:
            return False
        return bool(self.cfg.notify.get(key, False))

    # --- formatting + delivery ---

    def format_event(self, event: Event) -> str:
        lines = [f"[{event.severity.upper()}] {event.type}"]
        if event.job_id:
            lines.append(f"job: {event.job_id}")
        for k, v in (event.data or {}).items():
            lines.append(f"{k}: {v}")
        return "\n".join(lines)

    @abstractmethod
    def deliver(self, recipient: str, text: str, token: str | None) -> bool:
        ...

    def _deliver(self, recipient: str, text: str, token: str | None) -> bool:
        if self._sender is not None:
            return self._sender(recipient, text, token)
        return self.deliver(recipient, text, token)

    def send(self, event: Event, *, dry_run: bool = False) -> dict[str, Any]:
        """Route one event. Never raises; returns a structured result."""
        result: dict[str, Any] = {
            "adapter": self.name,
            "sent": 0,
            "skipped": None,
            "dry_run": dry_run,
            "error": None,
        }
        if not self.enabled():
            result["skipped"] = "disabled"
            return result
        if not self.should_notify(event):
            result["skipped"] = "notify_rule"
            return result
        recipients = self.recipients()
        if not recipients:
            result["skipped"] = "no_recipients"
            return result
        token = self.token()
        text = self.format_event(event)
        if dry_run:
            result["sent"] = 0
            result["skipped"] = "dry_run"
            result["would_send"] = len(recipients)
            return result
        sent = 0
        try:
            for r in recipients:
                if self._deliver(r, text, token):
                    sent += 1
        except Exception as exc:  # noqa: BLE001 - never break the core
            result["error"] = {"type": "delivery", "message": str(exc)}
        result["sent"] = sent
        return result

    def status(self) -> dict[str, Any]:
        """Adapter status with the token masked (never leak secrets)."""
        return {
            "name": self.name,
            "enabled": self.enabled(),
            "recipients": len(self.recipients()),
            "token": mask_secret(self.token()) if self.token() else None,
        }
