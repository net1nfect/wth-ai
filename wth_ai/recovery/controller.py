"""Error / recovery controller.

Classifies failures into the flowchart's error taxonomy and routes them to the
recovery mode (C11F): record an audit entry, decide whether to degrade to a
passive recovery posture, and whether a delta report should be emitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Flowchart error taxonomy (ERR1..ERR8).
ERROR_TYPES: tuple[str, ...] = (
    "scope_violation", "rate_limit_exceeded", "network_timeout", "plugin_failure",
    "state_anomaly", "sensitive_response", "worker_crash", "storage_failure",
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scope_violation", re.compile(r"(?i)out of scope|scope violation|forbidden|not in allowlist|denylist")),
    ("rate_limit_exceeded", re.compile(r"(?i)rate.?limit|429|too many requests")),
    ("network_timeout", re.compile(r"(?i)timed? ?out|timeout|connection (refused|reset|aborted)|max retries")),
    ("plugin_failure", re.compile(r"(?i)plugin")),
    ("sensitive_response", re.compile(r"(?i)sensitive|secret|credential leak|set-cookie.*token")),
    ("storage_failure", re.compile(r"(?i)no space|disk|permission denied|read-only file system|sqlite")),
    ("worker_crash", re.compile(r"(?i)worker|segmentation fault|killed|oom")),
    ("state_anomaly", re.compile(r"(?i)state|inconsistent|unexpected status|assertion")),
)

# Which error types force a drop to recovery (passive + audit only) posture.
_RECOVERY_TRIGGERS = {
    "scope_violation", "sensitive_response", "worker_crash", "storage_failure",
    "rate_limit_exceeded",
}


def classify_error(error: Any) -> str:
    text = str(error or "")
    for label, pat in _PATTERNS:
        if pat.search(text):
            return label
    return "state_anomaly"


@dataclass
class RecoveryDecision:
    error_type: str
    enter_recovery: bool
    emit_delta: bool
    action: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type, "enter_recovery": self.enter_recovery,
            "emit_delta": self.emit_delta, "action": self.action, "message": self.message,
        }


class RecoveryController:
    """Maps errors to recovery actions, with audit logging + optional events."""

    def __init__(self, *, audit_log: Any = None, bus: Any = None) -> None:
        self.audit_log = audit_log  # Callable[[str], None] | None
        self.bus = bus
        self.in_recovery = False

    def handle(self, error: Any, *, context: str = "") -> RecoveryDecision:
        etype = classify_error(error)
        enter = etype in _RECOVERY_TRIGGERS
        decision = RecoveryDecision(
            error_type=etype,
            enter_recovery=enter,
            emit_delta=etype in ("scope_violation", "sensitive_response"),
            action=("drop_to_passive_recovery" if enter else "retry_or_continue"),
            message=f"{etype} in {context or 'engine'}: {str(error)[:200]}",
        )
        if enter:
            self.in_recovery = True
        self._audit(decision)
        self._emit(decision)
        return decision

    def _audit(self, d: RecoveryDecision) -> None:
        line = f"{datetime.now(timezone.utc).isoformat()} recovery {d.error_type} action={d.action}"
        if callable(self.audit_log):
            try:
                self.audit_log(line)
            except Exception:  # noqa: BLE001
                pass

    def _emit(self, d: RecoveryDecision) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit("app.error", source="recovery",
                          data={"stage": "recovery", "reason": d.message,
                                "error_type": d.error_type, "action": d.action})
        except Exception:  # noqa: BLE001
            pass
