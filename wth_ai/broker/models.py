"""`ActionIntent`/`ExecutionResult` — broker-level execution vocabulary
(WU-102, PRD-011, ADR-008).

`ExecutionResult` mirrors `wth_ai.terminal.models.TerminalResult` field-for-
field (plus `intent_id`) so the translation in
`AgentTerminalBridge.dispatch_tool()` is a lossless 1:1 copy in both
directions -- no reconstruction logic needed, no data silently dropped on
the round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionIntent:
    intent_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    actor: str = "executor"
    approved: bool = False
    purpose: str = ""


@dataclass
class ExecutionResult:
    intent_id: str
    transcript_id: str
    actor: str
    purpose: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    blocked: bool = False
    block_reason: str = ""
    url: str = ""
    method: str = ""
    status_code: int = 0
