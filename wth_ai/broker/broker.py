"""`ExecutionBroker` — single execution chokepoint (WU-102, PRD-011, ADR-008).

Scope decision (P0 slice of PRD-011's own priority split: "P0 (skeleton +
AgentTerminalBridge facade) / P1 (integrasi penuh 5 jalur eksekusi)"):

`ExecutionBroker.execute()` here WRAPS `AgentTerminalBridge.run_shell()`/
`http_probe()` (composition, reusing their existing scope/permission/
denylist/approval/throttle logic UNCHANGED) rather than reimplementing the
8-stage pipeline described in the target architecture (schema validation ->
actor/trust resolution via `context.tainted` -> `Engagement` fetch-fresh ->
scope check -> `evaluate_intent()` -> Approval Gate action-hashing ->
rate-limit/concurrency -> sandbox+`RunningActionRegistry` -> execution ->
audit sinkron -> evidence auto-store). Only 3 of those stages have real,
already-built infrastructure to call today (scope check + evaluate_intent()
+ rate-limit -- all already inside `run_shell()`/`http_probe()`); the rest
depend on subsystems that don't exist yet:
  - actor/trust resolution (`context.tainted`) -- WU-007 remainder.
  - `Engagement` fetch-fresh -- needs `EngagementStore` (WU-101); WU-014's
    `Engagement` is a standalone skeleton with no store yet.
  - Approval Gate action-hashing -- WU-007 remainder (today: a plain
    `approved: bool`, not a canonical action-hash).
  - Sandbox + `RunningActionRegistry` -- WU-106.
  - Audit sinkron -- WU-105.
  - Evidence auto-store -- WU-114/115.

Building those stages now against non-existent infrastructure would be
hollow architecture (same discipline as WU-005's `evaluate_intent()`
layers). What THIS WU delivers, honestly: a real, working chokepoint that
`AgentTerminalBridge.dispatch_tool()` now calls through (satisfying PRD-011's
acceptance criterion "existing dispatch_tool() call sites unchanged"), ready
for other callers (`ExecuteRpcServer`, `executor/vuln/*.py`, `ReconEngine`)
to migrate onto in later WUs (WU-104), and a single place where the deferred
pipeline stages get added later without touching every caller again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wth_ai.broker.models import ActionIntent, ExecutionResult

if TYPE_CHECKING:
    from wth_ai.terminal.bridge import AgentTerminalBridge


class ExecutionBroker:
    def __init__(self, bridge: "AgentTerminalBridge") -> None:
        self.bridge = bridge

    def execute(self, intent: ActionIntent) -> ExecutionResult:
        if intent.tool_name == "run_shell":
            tr = self.bridge.run_shell(
                intent.arguments.get("command", ""),
                actor=intent.actor,
                purpose=intent.purpose,
                approved=intent.approved,
            )
        elif intent.tool_name == "http_probe":
            tr = self.bridge.http_probe(
                intent.arguments.get("method", "GET"),
                intent.arguments.get("url", ""),
                actor=intent.actor,
                purpose=intent.purpose,
                headers=intent.arguments.get("headers"),
                body=intent.arguments.get("body"),
                approved=intent.approved,
            )
        else:
            # NOTE: purpose=intent.tool_name here (not intent.purpose) --
            # preserves the exact positional args the pre-broker
            # dispatch_tool() fallback used (`self._blocked(tid, actor,
            # name, str(arguments), ...)`), so this refactor changes zero
            # observable behavior for the unknown-tool case.
            tid = self.bridge._new_id()
            tr = self.bridge._blocked(
                tid, intent.actor, intent.tool_name, str(intent.arguments),
                f"Unknown tool: {intent.tool_name}",
            )
        return ExecutionResult(
            intent_id=intent.intent_id,
            transcript_id=tr.transcript_id,
            actor=tr.actor,
            purpose=tr.purpose,
            command=tr.command,
            stdout=tr.stdout,
            stderr=tr.stderr,
            exit_code=tr.exit_code,
            blocked=tr.blocked,
            block_reason=tr.block_reason,
            url=tr.url,
            method=tr.method,
            status_code=tr.status_code,
        )
