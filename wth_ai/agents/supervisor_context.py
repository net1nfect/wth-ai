"""Shared context for supervisor tool dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from wth_ai.config import WTHConfig
from wth_ai.findings_db import FindingsDB
from wth_ai.graph.asset_graph import AssetGraph
from wth_ai.evidence.vault import EvidenceVault
from wth_ai.models import SessionState
from wth_ai.llm.router import LLMProviderRouter
from wth_ai.permission_manager import PermissionManager
from wth_ai.scope_manager import ScopeManager
from wth_ai.terminal.bridge import AgentTerminalBridge
from wth_ai.memory.action_memory import ActionMemory


@dataclass
class SupervisorCallbacks:
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None
    on_tool_end: Callable[[str, str, int], None] | None = None
    on_phase_change: Callable[[str, int], None] | None = None
    on_token: Callable[[str], None] | None = None
    on_approval_required: Callable[[str], None] | None = None
    on_llm_start: Callable[[str, str, str, dict[str, Any]], None] | None = None
    on_llm_tick: Callable[[str, int], None] | None = None
    on_llm_end: Callable[[str, str, int, dict[str, Any]], None] | None = None


@dataclass
class SupervisorContext:
    config: WTHConfig
    client: LLMProviderRouter
    state: SessionState
    scope_mgr: ScopeManager
    permission_mgr: PermissionManager
    asset_graph: AssetGraph
    evidence_vault: EvidenceVault
    findings_db: FindingsDB
    terminal: AgentTerminalBridge
    action_memory: ActionMemory | None = None
  # lazy refs set by orchestrator
    planner: Any = None
    executor: Any = None
    verifier: Any = None
    reporter: Any = None
    skill_router: Any = None
    job_manager: Any = None
    recon_bridge: Any = None
    approved_tasks: set[str] = field(default_factory=set)
    callbacks: SupervisorCallbacks = field(default_factory=SupervisorCallbacks)
    current_phase: str = "idle"
    tool_count: int = 0
    persist_fn: Callable[[], None] | None = None
    orchestrator: Any = None

    def notify_phase(self, phase: str, pct: int) -> None:
        self.current_phase = phase
        if self.callbacks.on_phase_change:
            self.callbacks.on_phase_change(phase, pct)

    def notify_tool_start(self, name: str, args: dict[str, Any]) -> None:
        self.tool_count += 1
        if self.callbacks.on_tool_start:
            self.callbacks.on_tool_start(name, args)

    def notify_tool_end(self, name: str, result: str, duration_ms: int) -> None:
        if self.callbacks.on_tool_end:
            self.callbacks.on_tool_end(name, result, duration_ms)

    def notify_llm_start(self, role: str, provider: str, model: str, meta: dict[str, Any] | None = None) -> None:
        if self.callbacks.on_llm_start:
            self.callbacks.on_llm_start(role, provider, model, meta or {})

    def notify_llm_tick(self, role: str, elapsed_s: int) -> None:
        if self.callbacks.on_llm_tick:
            self.callbacks.on_llm_tick(role, elapsed_s)

    def notify_llm_end(self, role: str, model: str, duration_ms: int, meta: dict[str, Any] | None = None) -> None:
        if self.callbacks.on_llm_end:
            self.callbacks.on_llm_end(role, model, duration_ms, meta or {})
