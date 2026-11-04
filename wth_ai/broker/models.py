"""Intent and result types for the execution broker (WU-102, PRD-011).

An :class:`ActionIntent` is what an agent asks for; an
:class:`ExecutionResult` is what actually happened, including the gate that
stopped it. Both are plain data so they can be logged, replayed, and linked to
claims without dragging the live bridge object along.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionIntent:
    """One requested action, with its authorization context."""

    intent_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    actor: str = "executor"
    approved: bool = False
    purpose: str = ""
    #: Claims this action is meant to substantiate (DATA-MODEL §5.1). Filled by
    #: the planner before dispatch; the broker does not resolve them itself.
    claim_ids: list[str] = field(default_factory=list)

    @property
    def command(self) -> str:
        """Best-effort human-readable form, for audit lines."""
        if self.tool_name in ("http_probe", "http_request", "probe"):
            method = str(self.arguments.get("method", "GET")).upper()
            return f"{method} {self.arguments.get('url', '')}".strip()
        return str(self.arguments.get("command", "") or self.tool_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "tool_name": self.tool_name,
            "actor": self.actor,
            "approved": self.approved,
            "purpose": self.purpose,
            "command": self.command,
            "claim_ids": list(self.claim_ids),
        }


@dataclass
class ExecutionResult:
    """Outcome of one :class:`ActionIntent`."""

    transcript_id: str = ""
    actor: str = ""
    purpose: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    blocked: bool = False
    block_reason: str = ""
    url: str = ""
    method: str = ""
    status_code: int = 0
    analysis: str = ""

    @classmethod
    def from_terminal(cls, result: Any, intent: ActionIntent | None = None) -> "ExecutionResult":
        """Translate a ``TerminalResult`` without losing a field."""
        return cls(
            transcript_id=getattr(result, "transcript_id", ""),
            actor=getattr(result, "actor", intent.actor if intent else ""),
            purpose=getattr(result, "purpose", intent.purpose if intent else ""),
            command=getattr(result, "command", ""),
            stdout=getattr(result, "stdout", ""),
            stderr=getattr(result, "stderr", ""),
            exit_code=getattr(result, "exit_code", 0),
            blocked=getattr(result, "blocked", False),
            block_reason=getattr(result, "block_reason", ""),
            url=getattr(result, "url", ""),
            method=getattr(result, "method", ""),
            status_code=getattr(result, "status_code", 0),
            analysis=getattr(result, "analysis", ""),
        )

    @classmethod
    def blocked_for(cls, intent: ActionIntent, reason: str) -> "ExecutionResult":
        return cls(
            actor=intent.actor,
            purpose=intent.purpose,
            command=intent.command,
            stderr=reason,
            exit_code=127,
            blocked=True,
            block_reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript_id": self.transcript_id,
            "actor": self.actor,
            "purpose": self.purpose,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "url": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "analysis": self.analysis,
        }
